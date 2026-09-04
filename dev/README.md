# Dev / testing database

A local Postgres-in-Docker setup for working on `dataregistry` without
touching the shared NERSC database.

## Requirements

- Docker (or Podman with `docker compose` shim) — the script uses `docker
  compose` and falls back to `docker-compose`.
- A Python env where `dataregistry` is importable (e.g. `pip install -e .`
  from the repo root).
- `GitPython` (`pip install GitPython`) — required by the schema-creation
  script.

## Quick start

```bash
# Start the container, create schemas, seed fake data.
./dev/dev-db.sh up

# Point your shell at the dev DB.
eval "$(./dev/dev-db.sh env)"

# Run something against it.
python -c "
import os
from dataregistry import DataRegistry
dr = DataRegistry(root_dir=os.environ['DATAREG_DEV_ROOT_DIR'])
print(dr.query.find_datasets(['dataset.name', 'dataset.version_string']))
"

# Stop the container (data volume is preserved across stop/start).
./dev/dev-db.sh down
```

## Subcommands

| Command                            | What it does                                                 |
| ---------------------------------- | ------------------------------------------------------------ |
| `./dev/dev-db.sh up [--no-seed]`   | Start container, create schemas if missing, run seed script  |
|                                    | (skip seed with `--no-seed`).                                |
| `./dev/dev-db.sh down`             | Stop the container. Data volume kept.                        |
| `./dev/dev-db.sh reset [--no-seed]`| Stop, delete the data volume + on-disk root_dir, then `up`.  |
| `./dev/dev-db.sh status`           | Show container status and pointer instructions.              |
| `./dev/dev-db.sh env`              | Print `export` lines for `eval`.                             |
| `./dev/dev-db.sh psql`             | Open a `psql` shell inside the container.                    |
| `./dev/dev-db.sh seed`             | Re-run the seed script (idempotent; logs what it skips).     |

## Running the test suite against the dev DB

The end-to-end tests assume a CI-shaped empty database (the seed data would
collide with test-created keywords and pollute broad queries). Use
`--no-seed`:

```bash
./dev/dev-db.sh reset --no-seed
export DATAREG_CONFIG=$(pwd)/dev/dataregistry.dev.yaml
export DATAREG_BACKEND=postgres
pytest -v tests/unit_tests
(cd tests/end_to_end_tests && pytest -v test_*.py)
```

`reset` rather than `up` guarantees a clean DB between test runs (CI gets a
fresh container per job; we have to do it manually).

## What lives where

- `docker-compose.yml` — Postgres 16 service, named volume, healthcheck.
- `dataregistry.dev.yaml` — sqlalchemy URL pointing at the container. The
  library picks it up via `DATAREG_CONFIG`.
- `seed_dev_data.py` — fills both `lsst_desc_working` and
  `lsst_desc_production` schemas with a small, realistic set of datasets,
  executions/dependencies, aliases, and custom keywords.
- `root_dir/` — created on `up`. Acts as the on-disk root for registered
  datasets (most seed entries are `location_type=dummy`, so no real bytes
  get copied around).

## Pointing the library at the dev DB

The library locates its database via `DATAREG_CONFIG`. `eval "$(./dev/dev-db.sh
env)"` sets that and `DATAREG_DEV_ROOT_DIR` for you; alternatively:

```bash
export DATAREG_CONFIG=$(pwd)/dev/dataregistry.dev.yaml
export DATAREG_DEV_ROOT_DIR=$(pwd)/dev/root_dir
```

The library's `site` machinery uses a YAML bundled inside the installed
package (`src/dataregistry/site_config/site_rootdir.yaml`) and has no env
override for that path, so we can't register a `dev` site. Always pass
`root_dir=` explicitly when constructing `DataRegistry` — the seed defaults
to `dev/root_dir`, which is what `DATAREG_DEV_ROOT_DIR` points at.

## Notes

- Re-running `up` is safe: schema creation is skipped if the schemas already
  exist, and the seed script keeps going past duplicate-entry errors. Use
  `reset` for a clean slate.
- Override the on-disk root_dir with `DATAREG_DEV_ROOT_DIR=/some/path
  ./dev/dev-db.sh up`.
- Port 5432 is published on the host; if you already have Postgres listening
  there, edit `docker-compose.yml` to remap.
