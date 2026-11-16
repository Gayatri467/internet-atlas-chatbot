# graph — superseded

`graph-compose.yaml` was the first attempt at converging the AS graph and the
prefix graph into one Neo4j. That job now lives in
[`../internet-mapper-api/compose.yaml`](../internet-mapper-api/compose.yaml),
which runs the same two loaders (`../graph-poc/loader`,
`../prefix-graph/loader`) as pipeline stages alongside the API and the UI:

```bash
cd ../internet-mapper-api && make demo
```

Both compose files publish Neo4j on 7474/7687, so **only one can run at a
time**. Bring this one down before using the unified stack:

```bash
docker compose -f graph-compose.yaml down
```

The empty `as-loader/` and `prefix-loader/` directories were placeholders; the
loaders were never moved here and the compose file builds them from the original
PoC directories.

Kept for reference only. New work goes in `../internet-mapper-api`.
