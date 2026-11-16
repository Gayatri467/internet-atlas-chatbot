## Steps

### Ne04J Browser 
- http://localhost:7474
  neo4j / internetmapper

### Build 
- docker compose up -d neo4j
- docker compose ps
- docker compose build graph-loader
- docker compose run --rm graph-loader
- docker compose run --rm graph-queries

### Input can be grabbed from 
cp ../converter-parquet/output/bgp_updates.parquet input/bgp_updates.parquet



