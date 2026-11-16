# FRR MRT Lab

Two-router eBGP labs that write MRT dumps (`updates`, `updates-tx`, `routes-mrt`)
to the host. There are two independent labs — **operator 1** and **operator 2** —
and two ways to run each: **containerlab** or **docker compose**.

| | Operator 1 | Operator 2 |
|---|---|---|
| Routers | `op1r1` (AS 64512) ↔ `op1r2` (AS 64513) | `op2r1` (AS 64515) ↔ `op2r2` (AS 64516) |
| Peering link | `192.168.100.1/29` ↔ `192.168.100.2/29` | `192.200.100.1/29` ↔ `192.200.100.2/29` |
| Advertised prefix | `10.10.0.0/24` (from `op1r1`) | `20.20.0.0/24` (from `op2r1`) |
| Configs | `configs-operator-1/` | `configs-operator-2/` |
| MRT output | `operator1_mrt/` | `operator2_mrt/` |

Both run-methods use the **same** config files, so pick whichever you prefer.

---

## 1. Build the image

Both labs need one image: `frr-local:mrt-tx` — upstream FRR at a pinned commit
plus `frr-bgp-mrt-v0.1.diff`, the `dump bgp updates-tx` patch carried in this
directory. Everything needed is in this repository:

```sh
cd frr-mrt

git clone git@github.com:FRRouting/frr.git
cd frr
git checkout d5a6ba7e0cae53f8ea65b98c470cf26f9dd077a4
patch -p1 < ../frr-bgp-mrt-v0.1.diff

docker build \
    --file=docker/alpine/Dockerfile \
    --build-arg=PKGVER=0 \
    --tag=frr-local:mrt-tx \
    .
```

~10–15 min cold, a couple of minutes cached.

Check it:

```sh
docker run --rm --entrypoint sh frr-local:mrt-tx -c '/usr/lib/frr/bgpd --version | head -1'
# bgpd version 10.8.0-dev_git0
```

### Notes on the build

- **The pinned commit is not optional.** The patch touches `bgpd/bgp_dump.c`,
  `bgpd/bgp_packet.c`, `docker/alpine/Dockerfile`, `doc/user/bgp.rst` and one
  topotest. It is generated against `d5a6ba7e` and will not apply cleanly to a
  moving `master`.
- **Clone inside `frr-mrt/`**, as above — that is what makes the relative
  `../frr-bgp-mrt-v0.1.diff` resolve. The `frr-mrt/frr/` clone is gitignored, so
  it will not show up as an untracked nested repository.
- **The build reads the working tree** (`COPY . /src`), so the patch does not
  need to be committed in the clone. `git apply ../frr-bgp-mrt-v0.1.diff` works
  in place of `patch -p1` if you prefer.
- **A prebuilt image is planned** so this build is not needed on every host. It
  is not published yet — build from source for now.

---

## 2a. Launch with containerlab

```sh
cd frr-mrt

sudo clab deploy -t bgp-mrt.operator2.clab.yml     # operator 2
sudo clab deploy -t bgp-mrt.operator1.clab.yml     # operator 1
```

From the repo root, `containerlab` works the same as `clab`:

```sh
sudo containerlab deploy  -t frr-mrt/bgp-mrt.operator2.clab.yml
sudo containerlab destroy -t frr-mrt/bgp-mrt.operator2.clab.yml
```

Tear down:

```sh
sudo clab destroy -t bgp-mrt.operator2.clab.yml --cleanup
```

Containers are named `clab-bgp-operator2_mrt-op2r1` / `-op2r2`
(operator 1: `clab-bgp-mrt-op1r1` / `-op1r2`).

## 2b. Launch with docker compose

```sh
cd frr-mrt

docker compose -f docker-compose.operator2.yml up -d      # operator 2
docker compose -f docker-compose.operator1.yml up -d      # operator 1
```

Tear down:

```sh
docker compose -f docker-compose.operator2.yml down
```

Containers are named plainly: `op2r1`, `op2r2` (and `op1r1`, `op1r2`).

Run **one** method at a time per operator — both use the same container names,
peering subnets and MRT directory.

---

## 3. Check the session is up

```sh
docker exec -it clab-bgp-operator2_mrt-op2r1 vtysh -c 'show ip bgp summary'
# docker compose:  docker exec -it op2r1 vtysh -c 'show ip bgp summary'
```

You want the peer in `Established` — a number under `State/PfxRcd`, not `Active`
or `Connect`:

```
Neighbor        V         AS   MsgRcvd   MsgSent   TblVer  InQ OutQ  Up/Down State/PfxRcd   PfxSnt
192.200.100.2   4      64516         4         5        1    0    0 00:00:25            0        1
```

And the prefix on the far side:

```sh
docker exec -it clab-bgp-operator2_mrt-op2r2 vtysh -c 'show ip bgp'
# *>  20.20.0.0/24     192.200.100.1     0    0 64515 i
```

---

## 4. Generate updates

Flap the advertised route on `op2r1` — withdraw it, then put it back. Each step
produces a real BGP UPDATE that lands in the MRT dumps.

Drop into the shell:

```sh
docker exec -it clab-bgp-operator2_mrt-op2r1 vtysh
```

Withdraw:

```
op2r1# conf t
op2r1(config)# no ip route 20.20.0.0/24 Null0
op2r1(config)# exit
```

Re-advertise:

```
op2r1# conf t
op2r1(config)# ip route 20.20.0.0/24 Null0
op2r1(config)# exit
```

Same thing without the interactive shell:

```sh
docker exec clab-bgp-operator2_mrt-op2r1 vtysh -c 'conf t' -c 'no ip route 20.20.0.0/24 Null0'
docker exec clab-bgp-operator2_mrt-op2r1 vtysh -c 'conf t' -c 'ip route 20.20.0.0/24 Null0'
```

For operator 1 the prefix is `10.10.0.0/24` on `op1r1`.

---

## 5. Read the MRT dumps

Files rotate every minute (`updates`, `updates-tx`) or every 5 minutes
(`routes-mrt`), per the `dump bgp` lines at the bottom of each `.conf`:

```sh
ls -l operator2_mrt/
# op2r1-updates-20260829-0649.mrt        rx
# op2r1-updates-tx-20260829-0649.mrt     tx
# op2r1-rib-20260829-0649.mrt            table dump
```

The files are mode `0600` owned by uid 100 inside the container (shows up as
`_apt` on the host), so read them through the container:

```sh
docker exec clab-bgp-operator2_mrt-op2r1 cat /var/log/frr/mrt/op2r1-updates-tx-20260829-0649.mrt > /tmp/tx.mrt
```

A flap should bump the `updates-tx` file from 0 bytes to ~146 bytes. If it stays
at 0, the session never came up — go back to step 3.

---

## Gotchas

- **`pull access denied for frr-local`** — the image isn't built. Run step 1. Both
  topologies reference `frr-local:mrt-tx`; docker only tries the registry when
  the tag is missing locally.
- **Peering links are `/29`, not `/30`.** Docker compose needs room for a gateway
  address in the subnet, which a `/30` has no space for. The `/29` keeps a single
  set of config files working under both launch methods.
- **Operator 1 compose vs. `mrt-tx-net`** — a leftover `docker network`
  (`192.168.100.0/24`) overlaps operator 1's peering subnet and compose will
  refuse to create it. Remove it if unused: `docker network rm mrt-tx-net`.
- **`Node name will not resolve via DNS`** — the `_` in the containerlab topology
  name `bgp-operator2_mrt`. Cosmetic; the lab works.
- **`Can't open configuration file /etc/frr/vtysh.conf`** — harmless, the image
  ships no `vtysh.conf`.
- **MRT bind dirs must stay world-writable** (`operator1_mrt/`, `operator2_mrt/`)
  — `frr` is uid 100 inside the image.
- **`sudo` and containerlab** — if your user is in `clab_admins` and the `docker`
  group, `clab deploy` works without `sudo`. Mixing the two leaves lab
  directories owned by different users, so pick one and stay with it.

---

## Appendix: compile-only check

Faster loop when you only want to know whether the tree builds — no runtime image.

```sh
cd /home/yogesh/frr-mrt-lab/frr

docker build \
    --file=docker/alpine/Dockerfile \
    --build-arg=PKGVER=0 \
    --tag=frr-src:mrt-tx \
    --target=source-builder \
    .

docker run --rm frr-src:mrt-tx sh -c 'cd /src && make -j"$(nproc)" bgpd/bgpd'
```

Note: `bgpd -C` on a config containing a `router bgp` stanza aborts during
teardown with `bgp_keepalives_off(): assertion (fpt->running) failed`. That is
pre-existing on master, not an MRT regression — the config has already parsed by
then, so a non-zero rc there is not a parse failure.

Cleanup — these build images are ~1.7 GB:

```sh
docker image rm frr-src:mrt-tx frr-built:mrt-tx
docker builder prune -f
```
