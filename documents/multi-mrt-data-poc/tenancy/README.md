# tenancy — running the private-operator BGP slice

Ingests two private network operators' MRT dumps, merges each with the
platform's public BGP data, proves neither operator can see the other, and runs
digital-twin validation against the merged view.

**Everything here is reproducible from an empty `work/`.** This document is the
operating manual; the design rationale lives in
[../docs/TENANCY.md](../docs/TENANCY.md), the measurements in
[../docs/PHASE-A-FINDINGS.md](../docs/PHASE-A-FINDINGS.md), and the demo
walkthrough in [../docs/demo.html](../docs/demo.html).

---

> **Presenting this?** [`all-demo/`](all-demo/) is the demo pack: tunnel, query
> packs for both tenants, and three scripts, with expected output at each step.

## 1. TL;DR

With prerequisites in place and both labs already running:

```sh
cd multi-mrt-data-poc/tenancy
make deps        # confirm the host is ready
make labdata     # flap tenant prefixes so there is MRT to ingest  (~2 min)
make all         # census -> extract -> replay -> views -> up -> graph -> test
```

Expected final line: **`26 checks, 0 failed`**.

Starting from nothing, work through sections 2 → 5 instead.

---

## 2. Prerequisites

Run `make deps` first — it checks every item below and prints the fix for
anything missing.

### Host tools

| Tool | Notes |
|---|---|
| `docker` + compose v2 | You must be in the `docker` group. The ingester reads root-owned MRT files through a throwaway container instead of `sudo`, so **docker group membership is required**, not optional. |
| `containerlab` (`clab`) | Only needed to run the operator labs. If you already have MRT dumps, you can skip it. |
| `python3` ≥ 3.10 | Stages run on the host, not in containers. |

### Python modules

```sh
pip3 install --user duckdb pyarrow pyyaml neo4j
```

Verified against duckdb 1.5.5, pyarrow 25.0.0, pyyaml 5.4.1, neo4j 6.3.0.

### Docker images

Four images: three built from this repository, one pulled.

#### 1. `frr-local:mrt-tx` — the operator routers

FRR with the `dump bgp updates-tx` extension: upstream FRR at a pinned commit,
plus [`frr-mrt/frr-bgp-mrt-v0.1.diff`](../../frr-mrt/frr-bgp-mrt-v0.1.diff),
which is carried in this repository. Full notes: `frr-mrt/README.md` step 1.

```sh
cd multi-mrt-data-poc/tenancy
make frr-image        # or `make frr` — same target
```

That runs [`frr-mrt/prepare-frr-mrt-image.sh`](../../frr-mrt/prepare-frr-mrt-image.sh),
which clones upstream FRR into `frr-mrt/frr`, pins the commit, applies the patch,
builds the image and prints the `bgpd` version.

**This is a one-time, per-host step — deliberately outside `make all`.** It takes
~10-15 min cold and the image is then reused by every lab run, so it is not part
of the routine pipeline. Re-running it is cheap and safe: an existing
`frr-local:mrt-tx` is detected and the build skipped (`FORCE=1 make frr-image`
rebuilds anyway). The script is re-runnable from any directory, reuses an
existing clone, and resets the tree before patching, so a half-finished earlier
attempt does not need cleaning up first.

By hand, if you would rather not use the target:

```sh
cd frr-mrt
git clone https://github.com/FRRouting/frr.git
cd frr
git checkout d5a6ba7e0cae53f8ea65b98c470cf26f9dd077a4
git apply ../frr-bgp-mrt-v0.1.diff
docker build --file=docker/alpine/Dockerfile --build-arg=PKGVER=0 \
             --tag=frr-local:mrt-tx .
```

Check it:

```sh
docker run --rm --entrypoint sh frr-local:mrt-tx \
  -c '/usr/lib/frr/bgpd --version | head -1'
# bgpd version 10.8.0-dev_git0
```

**Do not skip the `git checkout`.** The patch touches `bgpd/bgp_dump.c`,
`bgpd/bgp_packet.c`, `docker/alpine/Dockerfile`, `doc/user/bgp.rst` and one
topotest; it is pinned to `d5a6ba7e` and will not apply cleanly to a moving
`master`. Cloning into `frr-mrt/` puts the tree where `../frr-bgp-mrt-v0.1.diff`
resolves; that clone is gitignored. Building by hand from anywhere else leaves an
untracked `frr/` outside that ignore rule — the script always clones beside
itself, so it cannot land in the wrong place.

> **Prebuilt image — planned, not yet published.** The intent is to publish this
> image so it need not be rebuilt on every host. Until it is, build from source
> as above; there is no image to pull.

#### 2-3. Decoder and API

Both reused unmodified from the reference PoC — the tenancy slice never carries
its own copy of a parser, and only rebinds the API per tenant.

```sh
cd single-mrt-data-poc/mrt-decode           && docker compose build
cd single-mrt-data-poc/internet-mapper-api  && docker compose build api
```

#### 4. Pulled, not built

```sh
docker pull neo4j:5.26-community
docker pull alpine:3
```

### Public BGP sample

The platform's "General BGP Data" comes from a public collector dump at
`single-mrt-data-poc/mrt-decode/data/orig-updates.gz` (~5 MB). It is **not in
git**. If absent:

```sh
curl -fL --retry 3 -o single-mrt-data-poc/mrt-decode/data/orig-updates.gz \
  https://data.ris.ripe.net/rrc00/2026.01/updates.20260131.2300.gz
```

> Do not substitute `updates.gz` in that directory — on some checkouts it is a
> 128-byte stub, which yields a public dataset of exactly one prefix. The path
> is set in `config/tenants.yaml` under `public.source.path`.

---

## 3. Start the operator labs

Two independent two-router eBGP labs write MRT to `frr-mrt/operator{1,2}_mrt/`.

```sh
make labs        # or, by hand:
cd frr-mrt
clab deploy -t bgp-mrt.operator1.clab.yml
clab deploy -t bgp-mrt.operator2.clab.yml
```

**Use containerlab, not docker compose.** Both launch methods work, but compose
needs a docker network on the operator-1 peering subnet, and a leftover
`mrt-tx-net` (`192.168.100.0/24`) commonly collides with it. Containerlab wires
the link as a direct veth pair and sidesteps the conflict entirely.

Confirm the sessions are up — you want a **number** under `State/PfxRcd`, not
`Active` or `Connect`:

```sh
docker exec clab-bgp-mrt-op1r1 vtysh -c 'show ip bgp summary'
docker exec clab-bgp-operator2_mrt-op2r1 vtysh -c 'show ip bgp summary'
```

### What the labs advertise

| Tenant | Originating router | Prefixes | Why |
|---|---|---|---|
| operator1 | `op1r1` — **AS 12654** | `84.205.66.0/24` | public origin is 12654 → `ORIGIN_MATCH` |
| | | `187.249.2.0/24` | public origin is 32098 → `ORIGIN_MISMATCH` |
| | | `10.10.0.0/24` | absent from public data → `NOT_OBSERVED_GLOBALLY` |
| operator2 | `op2r1` — AS 64515 | `20.20.0.0/24` | private space; stays unobserved |

Operator 1 runs a real ASN and real prefixes **so that each twin validation
outcome actually fires** against real public data. The lab is sealed — two
containers on a veth pair, no external peering — so nothing is announced
anywhere. See `frr-mrt/configs-operator-1/op1r1.conf`.

---

## 4. Generate MRT to ingest

A freshly deployed lab converges once and then goes quiet: RIB files rotate, but
`updates`/`updates-tx` stay zero-byte because nothing changes. **Without this
step there is no update data at all** — the exact state operator 1 was found in.

```sh
make labdata                                    # 3 cycles, both tenants, ~2.5 min
python3 app/labdata.py --tenant operator1 --cycles 5   # or target one tenant
```

It withdraws and re-announces each tenant's prefixes on its originating router,
spacing flaps so they land in more than one 1-minute rotation. Routers,
containers and prefixes all come from `config/tenants.yaml`.

---

## 5. Run the pipeline

`make all` runs everything. To go stage by stage:

| Command | What it does | Time |
|---|---|---|
| `make census` | Decodes every MRT file and reports per-kind counts. Asserts that `size > 0` and "decodes to ≥1 element" agree. | ~2 min |
| `make extract` | MRT → tenant-tagged Parquet, one directory per tenant. | ~1 min |
| `make replay` | Route state + change log + advertised routes, per tenant. | **~10 min** |
| `make views` | Per-tenant merged views (`public ∪ own`) and twin views. | seconds |
| `make up` | Starts per-tenant Neo4j and per-tenant API processes. | ~1 min |
| `make graph` | Loads each tenant's own graph. | ~1 min |
| `make test` | Contract + isolation assertions. | ~1 min |

`make replay` dominates: replay is row-by-row, so the 50,000-element public
sample takes about ten minutes. The operators' own data replays in seconds — if
you only changed lab data, re-run just that tenant and skip the public pass:

```sh
python3 app/extract.py --tenant operator1
python3 app/replay.py  --tenant operator1
make views && make graph
```

---

## 6. Verify

```sh
make test
```

- **`test_contract.py`** — the element schema is still a strict superset of the
  reference PoC's, the change-type vocabulary still matches, and
  `single-mrt-data-poc/` source is unmodified.
- **`test_isolation.py`** — 26 assertions across DuckDB, Neo4j and both APIs,
  each with a positive control so an empty database cannot pass.

Two `NOTE` lines are expected and are **not** failures. They report that a
tenant's routing identifiers also appear in public data — which is correct, and
[../docs/TENANCY.md §6](../docs/TENANCY.md) explains why.

---

## 7. Explore

```sh
make status
```

| Tenant | API | Neo4j Bolt | Neo4j browser |
|---|---|---|---|
| operator1 | http://localhost:12351 | `bolt://localhost:7688` | http://localhost:7475 |
| operator2 | http://localhost:12352 | `bolt://localhost:7689` | http://localhost:7476 |

Neo4j credentials are `neo4j` / `operator1pass` and `operator2pass`.

### The twin queries

```sh
python3 - <<'EOF'
import duckdb
con = duckdb.connect('work/views/operator1/view.duckdb', read_only=True)
for r in con.execute("""
    SELECT prefix, advertised_origin, observed_origin, observing_peers, outcome
    FROM twin_origin_validation ORDER BY outcome""").fetchall():
    print(r)
EOF
```

Expect all three outcomes:

```
('10.10.0.0/24',   12654,  None, None, 'NOT_OBSERVED_GLOBALLY')
('84.205.66.0/24', 12654, 12654,   34, 'ORIGIN_MATCH')
('187.249.2.0/24', 12654, 32098,   20, 'ORIGIN_MISMATCH')
```

Thirty-four real RouteViews peers agree that `84.205.66.0/24` originates in
AS 12654; twenty say `187.249.2.0/24` originates in AS 32098, contradicting the
operator and firing `twin_possible_hijack`.

Other views in the same database: `twin_advertised`, `twin_possible_hijack`,
`twin_not_propagated`, `twin_multi_origin`, `twin_my_peers`,
`twin_global_view_of_me`.

### Connecting from another machine

The published ports bind to `0.0.0.0`, so they are reachable over the network
already — swap `localhost` for the host's LAN IP.

**Neo4j Browser defaults to the `neo4j://` scheme, and that needs one setting.**
`neo4j://` performs routing discovery: the server hands the *client* an address
to connect back on, taken from `server.bolt.advertised_address`. Neo4j's default
is `localhost:7687`, which tells a remote laptop to connect to **itself** — the
symptom is:

```
ServiceUnavailable: Could not perform discovery. No routing servers available.
Known routing table: RoutingTable[... routers=[], readers=[], writers=[]]
```

Two ways out, and you can use either:

1. **Use `bolt://` instead**, which skips discovery entirely and needs no server
   change. In the Neo4j Browser connect dialog, set the URL to
   `bolt://<host-ip>:7688`.
2. **Advertise a reachable address**, so `neo4j://` works too. Set
   `ATLAS_ADVERTISED_HOST` in [`.env`](.env) to the host's LAN IP and restart:

   ```sh
   echo 'ATLAS_ADVERTISED_HOST=10.0.0.5' > .env   # your host's IP
   docker compose up -d
   ```

   Confirm the server now hands out the right address:

   ```sh
   docker exec atlas-neo4j-operator1 cypher-shell -u neo4j -p operator1pass \
     "CALL dbms.routing.getRoutingTable({}, 'neo4j') YIELD servers RETURN servers;"
   ```

   The published port (7688/7689) must appear, not the in-container 7687.

The tenant APIs reach Neo4j over the compose network with `bolt://`, so they
never consult the routing table and are unaffected by this setting.

If a port is unreachable from the laptop, it is a network issue, not a config
one — check `nc -zv <host-ip> 7688`, then the host firewall
(`sudo ufw status`).

### Over an SSH tunnel

A tunnel avoids firewall changes entirely. Forward one port per service you
want, including a Bolt port per tenant:

```sh
ssh -N -L 7475:localhost:7475 -L 7688:localhost:7688 \
       -L 7476:localhost:7476 -L 7689:localhost:7689 \
       -L 12351:localhost:12351 -L 12352:localhost:12352 user@<host-ip>
```

**Use `bolt://` over a tunnel — `neo4j://` cannot work.** The two fixes above
pull in opposite directions: setting `ATLAS_ADVERTISED_HOST` to the LAN IP is
what makes `neo4j://` work for a *direct* client, and it is exactly what breaks
it through a tunnel, because the tunnel maps only `localhost` and the client is
handed the LAN IP instead. `bolt://` ignores the advertised address, so it is
the one form that works in **both** setups — prefer it always.

In Neo4j Browser, note that two defaults are wrong for a tunnel: the scheme
(`neo4j://`) and the port (`7687`, which is not forwarded). Set the Connect URL
to `bolt://localhost:7688` for operator1, `bolt://localhost:7689` for operator2.

| Setup | `bolt://` | `neo4j://` |
|---|---|---|
| Direct to LAN IP, `ATLAS_ADVERTISED_HOST` set | works | works |
| Direct to LAN IP, advertised left as `localhost` | works | fails — discovery returns `localhost` |
| SSH tunnel | works | fails — discovery returns the LAN IP |

> **These services have no meaningful access control.** Neo4j uses placeholder
> passwords from `compose.yaml`, and the tenant APIs have **no authentication at
> all** — anyone who can reach `<host>:12351` gets operator1's full merged view.
> On an untrusted network prefer the SSH tunnel, and bind the published ports to
> loopback by changing `"7688:7687"` to `"127.0.0.1:7688:7687"` in
> `compose.yaml`.

### Through the API

```sh
curl -s http://localhost:12351/api/v1/prefix/84.205.66.0%2F24 | python3 -m json.tool
curl -s http://localhost:12352/api/v1/stats | python3 -m json.tool
```

Prefixes are URL-encoded in paths. `GET /openapi.yaml` is the full spec and
`/ui/` is the bundled explorer.

---

## 8. Add a tenant

1. Add a block to `config/tenants.yaml`: `tenant_id`, `mrt_dir`, `owns`
   (ASNs and prefixes), `routers`, `graph` (Bolt endpoint + credentials), `api`
   (base URL).
2. Add the matching `neo4j-<tenant>` and `api-<tenant>` services to
   `compose.yaml`, on unused ports.
3. `make all`.

Every stage and both test suites read the registry, so nothing else hard-codes a
tenant. `tenant_id` becomes a directory name and is validated on load.

---

## 9. Troubleshooting

**`Conflicting lock is held` from DuckDB** — a previous run left a process
holding the state database. `pkill -f app/replay.py`, then re-run. DuckDB takes
an exclusive lock: while a writer holds it, even read-only opens fail.

**`pull access denied for frr-local`** — the FRR image was never built. Run
`make frr-image` (section 2, step 1).

**Census reports zero files** — the operator directory is empty, or the labs
were never deployed. Check `docker ps` for `clab-bgp-*` containers.

**`updates`/`updates-tx` all zero-byte** — the lab converged and went quiet. Run
`make labdata` (section 4).

**`Can't open configuration file /etc/frr/vtysh.conf`** — harmless. The FRR image
ships no `vtysh.conf`; the command still applied.

**`Node name will not resolve via DNS`** — cosmetic, from the `_` in the
operator-2 topology name.

**API returns `converting NULL to int64 is unsupported`** — the extract step ran
without origin derivation. Re-run `make extract` and `make replay`; locally
originated RIB entries need `origin_derived`.

**A twin prefix unexpectedly reports `NOT_OBSERVED_GLOBALLY`** — check whether it
is a *routing beacon*. Beacons announce and withdraw on a schedule and may end
the sample withdrawn, so they are legitimately absent from current route state.
`84.205.80.0/24` behaves this way; `84.205.66.0/24` does not.

**Ports already in use** — 7475/7476, 7688/7689, 12351/12352. Note the reference
`internet-mapper-api` stack uses 7474/7687/12345 and can run alongside. Because
both stacks are up at once, a driver pointed at `localhost:7687` reaches the
**reference** database, not a tenant's — check the prefix count (a tenant graph
has thousands; the reference sample has one) if results look wrong.

**`Could not perform discovery. No routing servers available.`** — a remote
client used `neo4j://` and was handed an address it cannot reach: `localhost`
when connecting directly, or the LAN IP when connecting through a tunnel. Switch
the Connect URL to `bolt://…`, which skips discovery. See §7, "Connecting from
another machine".

**Neo4j Browser connects to nothing over a tunnel** — its defaults are
`neo4j://` and port `7687`. Both are wrong here: use `bolt://localhost:7688`
(operator1) or `bolt://localhost:7689` (operator2), and make sure that Bolt port
is in your `-L` forwards.

---

## 10. Teardown

```sh
make down        # stop tenant Neo4j + API, keep volumes and derived data
make clean       # delete derived data in work/, keep containers
make nuke        # both, plus Neo4j volumes

cd ../../frr-mrt
clab destroy -t bgp-mrt.operator1.clab.yml --cleanup
clab destroy -t bgp-mrt.operator2.clab.yml --cleanup
```

`work/` is gitignored and fully regenerable.

---

## 11. Layout

```
config/tenants.yaml    the ONLY place a tenant is declared
app/tenants.py         the ONLY place it is parsed
app/mrtreader.py       reads 0600/uid-100 dumps via a root container, dir mounted :ro
app/labdata.py         flaps tenant prefixes to generate MRT
app/census.py          decode census — measures, assumes nothing
app/schema_ext.py      imports the reference schema, adds 4 provenance columns
app/extract.py         MRT -> tenant-tagged elements Parquet
app/replay.py          route state (rx) + advertised routes (tx)
app/views.py           per-tenant merged views, twin views, API exports
app/graph.py           loads each tenant's own Neo4j instance
bin/check-deps.py      prerequisite check behind `make deps`
test/test_contract.py  no drift from the reference PoC
test/test_isolation.py 26 assertions across three stores
compose.yaml           per-tenant Neo4j + per-tenant bound API
work/                  derived data — gitignored, regenerate with `make all`
```

## 12. What this is not

No workflow engine, no run ledger, no atomic-rename publish, no set-based
replay, no per-tenant enrichment, and **no authentication** — the tenant APIs are
separated by port number only. Ports are not a security boundary. Those gaps are
[../docs/PLAN.md](../docs/PLAN.md)'s scope and
[../docs/TENANCY.md](../docs/TENANCY.md) §8–9.
