# datasette data.csv prototypes

Experiments for loading CSV, TSV, JSON and newline-delimited JSON files,
URLs and stdin with `datasette data.csv`. These scripts are not part of the
package: they back up the measurements behind the design. The library code
they exercise is in `datasette/imports/`.

## Test data

Generate the shared test data (about 1.1 GB, takes about a minute):

```bash
uv run python prototypes/data_csv/generate_test_data.py /tmp/data-csv
```

Most scripts take that directory, or a file in it, as an argument. Usage is
at the top of each script, and the `bench_*` scripts also accept `--help`.

## Scripts

| Script | What it measures |
| --- | --- |
| `sniff_experiment.py` | Loader scores and winners per file, sniff-window sweep, edge cases, messy data |
| `loader_benchmark.py` | Loader throughput, peak RSS, cost of the cp1252 fallback codec |
| `inserter_affinity_probe.py` | What SQLite type affinity does to CSV strings, compared with `classify()` and sqlite-utils' `TypeTracker` |
| `bench_inserter.py` | Inserter throughput by batch size, memory vs WAL file, against sqlite-utils baselines |
| `bench_inserter_widening.py` | Cost of widening a column in place and by the rebuild fallback, on 100k to 6.4M rows |
| `bench_inserter_overhead.py` | Per-batch overhead of `import_rows()` through `execute_write_fn()` |
| `bench_inserter_concurrency.py` | Readers querying during an import, by storage type |
| `bench_inserter_fix_cost.py` | Cost of keeping original text for lossless widening |
| `fuzz_inserter_boundaries.py` | Checks that results don't depend on sample or batch boundaries |

## Headline results

- A shared-cache in-memory database (`file:scratch?mode=memory&cache=shared`)
  can't be queried during an import: the import itself fails with "database
  table is locked". A WAL database, temporary or on disk, had no errors.
- GeoJSON loaders beat plain JSON on every FeatureCollection tried when the
  sniffer checks structure with `peek_json()` rather than searching for
  strings.
- Through Datasette's write queue the inserter does about 58,000 rows/s with
  batches of 100 and 99,000 to 125,000 with batches of 1,000.
