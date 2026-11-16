# all-demo — the full demo, start to finish

Everything needed to show the tenant-isolated BGP twin from a laptop over an SSH
tunnel. Roughly **15 minutes** to present.

Every command and query here was run against the live stack; the expected output
noted at each step is what it actually printed, not an illustration.

| File | Runs on | What it shows |
|---|---|---|
| [`00-tunnel.sh`](00-tunnel.sh) | **laptop** | Opens the SSH tunnel |
| [`01-operator1-browser.cypher`](01-operator1-browser.cypher) | laptop browser | operator1's graph — the hijack, originate vs export |
| [`02-operator2-browser.cypher`](02-operator2-browser.cypher) | laptop browser | operator2's graph — the other side of the wall |
| [`03-twin-duckdb.sh`](03-twin-duckdb.sh) | **host** | Origin validation: advertised vs observed |
| [`04-api.sh`](04-api.sh) | laptop or host | Both tenant APIs, including a 404 that proves isolation |
| [`05-isolation-proof.sh`](05-isolation-proof.sh) | laptop or host | The isolation proof, side by side |

---

## Endpoints — read this before connecting

The two tenants differ in **port and password**, and mixing them is the most
common stumble:

| Tenant | Browser | Bolt Connect URL | Password |
|---|---|---|---|
| **operator1** | http://localhost:7475/browser/ | `bolt://localhost:7688` | `operator1pass` |
| **operator2** | http://localhost:7476/browser/ | `bolt://localhost:7689` | `operator2pass` |

Username is `neo4j` for both.

> **Use `bolt://`, not `neo4j://`.** Neo4j Browser defaults to `neo4j://` on port
> `7687` — both wrong here. `neo4j://` performs routing discovery, and the server
> answers with an address the tunnel does not carry, giving
> *"Could not perform discovery. No routing servers available."* `bolt://` skips
> discovery entirely and works over the tunnel and on the LAN alike.

---

## Step 0 — open the tunnel *(laptop)*

```sh
ssh -N -L 7475:localhost:7475 -L 7688:localhost:7688 \
       -L 7476:localhost:7476 -L 7689:localhost:7689 \
       -L 12351:localhost:12351 -L 12352:localhost:12352 yogesh@172.16.9.5
```

Or `./00-tunnel.sh`. Leave it running in its own terminal — `-N` means it opens
no shell, so it will simply sit there silently. That is success.

On Windows use the built-in OpenSSH client from PowerShell; the command is
identical. Check it with `curl http://localhost:12351/health` from a second
terminal.

**Prerequisite:** the stack must already be up on the host
(`make -C tenancy up`). The tunnel forwards ports; it does not start anything.

---

## Step 1 — operator1's graph *(laptop browser)*

Open **http://localhost:7475/browser/** and connect with
`bolt://localhost:7688`, `neo4j` / `operator1pass`.

Paste queries from [`01-operator1-browser.cypher`](01-operator1-browser.cypher)
**one at a time** — Browser runs a single statement per execution.

The narrative arc:

1. **1.1** public and private data living in one view, told apart by `data_scope`
2. **1.2** all three validation outcomes in a single result
3. **1.3 → 1.4** the hijack, as a table then **as a picture**
4. **1.5** the receipts — real RouteViews peers and real AS paths
5. **1.6 → 1.7** a provider *originating* its own prefix vs *exporting* a learned one
6. **1.8 → 1.9** the same transit shape in the lab and at internet scale
7. **1.10** operator2's prefix returns `0`

**The moment to pause on** is 1.4: two AS nodes both claiming one prefix.
operator1 says it originates `187.249.2.0/24`; twenty real public peers say
AS 32098 does.

---

## Step 2 — operator2's graph *(second browser tab)*

Open **http://localhost:7476/browser/** — note **7476**, not 7475 — and connect
with `bolt://localhost:7689`, `neo4j` / `operator2pass`.

Keep both tabs open. Run [`02-operator2-browser.cypher`](02-operator2-browser.cypher)
query **2.3** next to operator1's **1.3**:

| Query | operator1 sees | operator2 sees |
|---|---|---|
| Who originates `187.249.2.0/24`? | AS 12654 *(private)* **and** AS 32098 *(public)* | AS 32098 *(public)* **only** |

Same prefix, same question, two tenants, different answers. operator1's claim
about its own network is invisible to operator2 — while the public half is
identical for both.

Then **2.4** and **2.5** together make the security model precise:

- `10.10.0.0/24` → **0 nodes.** operator1 never propagated it, so it exists only
  in operator1's own data, and operator2 has no path to it.
- `84.205.66.0/24` → **visible, scope `public`.** Not a leak. It is in the global
  routing table, so every tenant sees it. What operator2 cannot see is
  operator1's *own observation* of it — which is why the scope reads `public`
  here and `both` in operator1's graph.

> **The rule, in one line:** an operator's prefixes are public knowledge; its
> *view* of them is not.

---

## Step 3 — origin validation *(host)*

```sh
cd ~/sw_builds/internet-atlas/multi-mrt-data-poc/tenancy
./all-demo/03-twin-duckdb.sh
```

Section 1 is worth reading aloud — the same prefix, the same instant, two
routers behaving differently:

```
router  prefix          as_path         behaviour
op1r1   84.205.66.0/24  [12654]         ORIGINATES its own
op1r2   84.205.66.0/24  [64513, 12654]  EXPORTS learned from AS 12654
```

Section 2 is the payoff — all three outcomes against real RouteViews data:

```
prefix          advertised  observed  peers  outcome
10.10.0.0/24    12654       None      None   NOT_OBSERVED_GLOBALLY
84.205.66.0/24  12654       12654     34     ORIGIN_MATCH
187.249.2.0/24  12654       32098     20     ORIGIN_MISMATCH
```

Section 6 runs the identical query for operator2 and returns one clean
`NOT_OBSERVED_GLOBALLY` with an empty hijack view — a healthy network next to a
contradicted one.

---

## Step 4 — through the API *(either machine)*

```sh
./all-demo/04-api.sh
```

Six steps; the pair that matter are 3–4 and 5–6:

- operator1 asks for its own `10.10.0.0/24` → **answered**
- operator2 asks for the same prefix → **HTTP 404**
- operator1 asks for `187.249.2.0/24` → origins **`[12654, 32098]`**
- operator2 asks for the same → origins **`[32098]`**

Same code, same image, same request. The only difference is which files and
which graph that process was bound to at startup — which is the whole isolation
mechanism.

---

## Step 5 — the isolation proof *(either machine)*

```sh
./all-demo/05-isolation-proof.sh
```

Four blocks: the differing answers, `0` nodes in each direction for private-only
prefixes, shared public data shown as *not* a leak, and a check that no reserved
AS 0 pseudo-peer exists in either graph.

For the auditable version, run the test suite itself:

```sh
make -C .. test        # 26 assertions across DuckDB, Neo4j and both APIs
```

Two `NOTE` lines are expected and are **not** failures — see
[../../docs/TENANCY.md](../../docs/TENANCY.md) §6.

---

## What to say about honesty

Two caveats belong in the telling, not the footnotes:

**The lab is sealed and the scenario is deliberate.** operator1 was configured to
announce `187.249.2.0/24` so the mismatch fires. Two containers on a virtual
link, no external peering — nothing is announced anywhere. This proves the
*detection logic is correct*; it is not evidence anyone is hijacking that prefix.
The public half of every comparison is genuine RouteViews data.

**There is no authentication.** The tenants are separated by port number. Ports
are not a security boundary. The isolation being demonstrated is of *data paths*
— separate files, separate database processes, separate API processes — not of
*access control*, which is the first thing to build before this faces a real
tenant.

---

## If something does not work

| Symptom | Cause |
|---|---|
| *"Could not perform discovery. No routing servers available."* | Browser used `neo4j://`. Change the Connect URL to `bolt://localhost:7688`. |
| Browser connects to nothing | Port `7687` is the default and is not forwarded. Use `7688` / `7689`. |
| `Unauthorized` | Passwords are per tenant: `operator1pass` on 7475/7688, `operator2pass` on 7476/7689. |
| Tunnel opens, nothing responds | The stack is not running on the host. `make -C tenancy up`. |
| A query returns nothing | Check which tenant's browser you are in — the packs are not interchangeable. |

Fuller troubleshooting: [../README.md](../README.md) §9.
