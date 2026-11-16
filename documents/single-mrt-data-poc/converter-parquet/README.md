## Build & Run Steps

- docker compose run --rm -T mrt-reader   /data/updates.gz   100   > ../converter-parquet/input//parsed_mrt.txt
- docker compose build parquet-converter
- docker compose run --rm parquet-converter
