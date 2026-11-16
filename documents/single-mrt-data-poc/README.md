# End-to-end demo

Everything below documents the PoCs individually, each with its own compose file
and hand-copied inputs. To run the **whole chain as one thing** — MRT → parquet
→ route state → Neo4j → API → browser UI — use `internet-mapper-api`, which
orchestrates these same PoC directories with a shared `work/` handoff directory
and no `cp` steps:

```bash
cd internet-mapper-api
make demo                        # fetch, build, ingest, serve, walk every endpoint
open http://localhost:12345/ui/
```

See [internet-mapper-api/README.md](internet-mapper-api/README.md). It replaces
`graph/graph-compose.yaml` (see [graph/README.md](graph/README.md)); the two
cannot run at once because both publish Neo4j on 7474/7687.

The sections below remain the reference for running each stage on its own.

---

## mrt-decode
---

#### Steps to build and run
- cd mrt-decode
- curl -L -o data/updates.gz http://data.ris.ripe.net/rrc00/2026.01/updates.20260131.2300.gz
- docker compose build
- docker compose run --rm mrt-reader (output on screen)

#### Output logs
000018 | A|1769900400|45.134.89.1|34927|189.1.172.0/22|34927 56655 6939 3356 262287|IGP|45.134.89.1|0|0|6939:7422 6939:8578 6939:9002 56655:2100|false||
000019 | A|1769900400|45.134.89.1|34927|189.1.172.0/23|34927 56655 6939 3356 262287|IGP|45.134.89.1|0|0|6939:7422 6939:8578 6939:9002 56655:2100|false||
000020 | A|1769900400|45.134.89.1|34927|70.34.166.0/24|34927 56655 6939 26554 393483|IGP|45.134.89.1|0|0|6939:7496 6939:8840 6939:9001 56655:2100|false||


## converter-parquet
---

### Get the input from mrt-reader
- cd mrt-decode; docker compose run --rm -T mrt-reader   /data/updates.gz   100   > ../converter-parquet/input//parsed_mrt.txt; cd ..
- cd converter-parquet
- docker compose build parquet-converter
- docker compose run --rm parquet-converter

### Output logs

#### Shell Logs

```
docker compose run --rm parquet-converter
Container converter-parquet-parquet-converter-run-829155b9e272 Creating 
Container converter-parquet-parquet-converter-run-829155b9e272 Created 
{
  "input": "/input/parsed_mrt.txt",
  "output": "/output/bgp_updates.parquet",
  "rows": 100,
  "columns": 22,
  "row_groups": 1,
  "compression": "zstd",
  "schema": "event_time: timestamp[us, tz=UTC] not null\nevent_timestamp_seconds: double not null\ncollector: string not null\nelement_type: string not null\npeer_ip: string not null\npeer_asn: uint32 not null\nprefix: string not null\nis_ipv6: bool notnull\nas_path_raw: string not null\nas_path: list<item: uint32> not null\n  child 0, item: uint32\norigin_asn: uint32\norigin_type: string\nnext_hop: string\ncommunities: list<item: string> not null\n  child 0, item: string\nis_withdrawal: bool not null\nmed: uint32\nlocal_pref: uint32\natomic_aggregate: bool not null\naggregator_asn: uint32\naggregator_ip: string\nsource_file_id: string not null\nsource_record_offset: int64 not null"
}
```

#### Parquet file
- output/bgp_updates.parquet


## graph-poc

### Grab the input
cp ../converter-parquet/output/bgp_updates.parquet input/bgp_updates.parquet

### Build and run
- docker compose up -d neo4j
- docker compose ps
- docker compose build graph-loader
- docker compose run --rm graph-loader

```
{
  "parquet_file": "/input/bgp_updates.parquet",
  "announcement_rows_read": 82,
  "unique_as_nodes_derived": 79,
  "unique_directed_edges_derived": 104,
  "graph_nodes": 79,
  "graph_edges": 104,
  "relationship_type": "ADJACENT_TO",
  "load_semantics": "idempotent MERGE"
}

```

- docker compose run --rm graph-queries

```
=== GRAPH COUNTS ===
as_nodes, adjacency_edges
79, 104

=== TOP ASNs BY OBSERVED DEGREE ===
asn, degree
1299, 13
...

=== MOST FREQUENT ADJACENCIES ===
source_asn, target_asn, observations
34927, 56655, 27
...

=== SAMPLE TWO-HOP PATHS ===
as_path
[34927, 174, 16509]
...
```

## Route-state

### Build & Run
- cp ../converter-parquet/output/bgp_updates.parquet \
   input/bgp_updates.parquet
- docker compose build route-state
- docker compose run --rm route-state

```
[notice] To update, run: pip install --upgrade pip

=== COUNTS ===
 current_routes  changes  processed
             63      100        100

=== CHANGE TYPES ===
      change_type  events
        NEW_ROUTE      70
     PATH_CHANGED      12
WITHDRAW_NO_STATE      11
        WITHDRAWN       7

=== CURRENT ROUTES ===
   collector     peer_ip  peer_asn          prefix                                    as_path  origin_asn  state_version                event_time
route-views4   12.0.1.63      7018 63.254.161.0/24                        [7018, 1299, 20326]       20326              1 2026-01-31 23:00:00+00:00
route-views4   12.0.1.63      7018  154.0.143.0/24          [7018, 1299, 16637, 20294, 37063]       37063              1 2026-01-31 23:00:00+00:00
...
...

=== PATH CHANGES ===
               event_time    collector  peer_asn           prefix                                 previous_as_path                                    new_as_path
2026-01-31 23:00:00+00:00 route-views4     22652   146.66.28.0/22                      [22652, 1299, 47605, 43016]     [22652, 33891, 24940, 16302, 51728, 43016]
2026-01-31 23:00:00+00:00 route-views4     34927   45.173.54.0/24       [34927, 1299, 12956, 22927, 52361, 267825]      [34927, 174, 12956, 22927, 52361, 267825]
...
...

=== WITHDRAWALS ===
               event_time    collector  peer_asn         prefix previous_as_path
2026-01-31 23:00:00+00:00 route-views4     51225 84.205.80.0/24  [198265, 12654]
2026-01-31 23:00:00+00:00 route-views4     51225 84.205.80.0/24  [198265, 12654]
...

```

## Prefix Graph

### Inputs

cp ../route-state/output/current_route_state.parquet \
   input/current_route_state.parquet
   
### Build and Run
- docker compose build prefix-loader
- docker compose run --rm prefix-loader
- docker compose run --rm graph-queries

```
 docker compose run --rm graph-queries
[+]  1/1t 1/11
 ? Container prefix-graph-neo4j-1 Running                                                                  0.0s
Container prefix-graph-neo4j-1 Waiting 
Container prefix-graph-neo4j-1 Healthy 
Container prefix-graph-graph-queries-run-e330157df079 Creating 
Container prefix-graph-graph-queries-run-e330157df079 Created 
as_nodes, prefixes, origin_edges, peer_prefix_observations
32, 51, 51, 63
origin_asn, current_prefixes
26554, 14
37440, 7
...
address_family, prefix_length, prefix_count
4, 21, 1
4, 22, 5
...
peer_asn, collector, visible_prefixes
34927, "route-views4", 29
204092, "route-views4", 8
...
```

- Run the queries
```
./queries/run-one.sh queries/01_counts.cypher
./queries/run-one.sh queries/02_top_origin_asns.cypher
./queries/run-one.sh queries/03_prefix_lookup.cypher
./queries/run-one.sh queries/04_asn_prefixes.cypher
./queries/run-one.sh queries/05_multi_origin_prefixes.cypher
./queries/run-one.sh queries/06_prefixes_by_length.cypher
./queries/run-one.sh queries/07_peer_visibility.cypher
./queries/run-one.sh queries/08_graph_visualization.cypher
```

## Converged graph

### Build and run
docker compose -f graph-compose.yaml up -d neo4j
docker compose -f graph-compose.yaml ps

docker compose -f graph-compose.yaml build as-graph-loader
docker compose -f graph-compose.yaml run --rm as-graph-loader
  -> (:AutonomousSystem)-[:ADJACENT_TO]->(:AutonomousSystem)

docker compose -f graph-compose.yaml build prefix-graph-loader
docker compose -f graph-compose.yaml run --rm prefix-graph-loader
  -> (:AutonomousSystem)-[:ORIGINATES]->(:Prefix)
  -> (:AutonomousSystem)-[:SEEN_VIA]->(:Prefix)
  
MATCH (a:AutonomousSystem)
RETURN count(a) AS autonomous_systems;

MATCH (p:Prefix)
RETURN count(p) AS prefixes;

MATCH ()-[r:ADJACENT_TO]->()
RETURN count(r) AS adjacency_edges;

MATCH ()-[r:ORIGINATES]->()
RETURN count(r) AS origin_edges;

MATCH ()-[r:SEEN_VIA]->()
RETURN count(r) AS peer_prefix_views;

MATCH path =
  (source:AutonomousSystem)-[:ADJACENT_TO]->
  (origin:AutonomousSystem)-[:ORIGINATES]->
  (prefix:Prefix)
RETURN path
LIMIT 100;

docker compose -f graph-compose.yaml run --rm as-graph-queries
docker compose -f graph-compose.yaml run --rm prefix-graph-queries

docker volume rm graph-poc_neo4j_data
docker volume rm graph-poc_neo4j_logs
docker volume rm prefix-graph_neo4j_data
