"""
Seed the local dev data registry with a small but realistic dataset.

Designed to be run against the Postgres container started by
`dev/dev-db.sh up`, after `scripts/create_registry_schema.py` has created the
schemas. Uses the public DataRegistry API so it stays compatible with schema
evolution.

The seed exercises every table:
  - dataset, execution, dependency (via input_datasets)
  - dataset_alias
  - keyword + dataset_keyword (a few custom keywords plus the presets)
  - both `working` and `production` schemas
"""

import argparse
import logging
import os

from dataregistry import DataRegistry
from dataregistry.schema import DEFAULT_NAMESPACE


# A small fixed catalog so reruns produce the same logical data set. Mixing
# owner_types, versions, and a few external/meta_only datasets gives queries
# something interesting to chew on.
DATASETS_WORKING = [
    # (name, version, owner, owner_type, description, location_type, keywords)
    (
        "DESC:cosmodc2:photometry",
        "1.0.0",
        "cosmodc2-team",
        "group",
        "CosmoDC2 mock photometry catalog, v1 baseline.",
        "dummy",
        ["simulation", "dc2"],
    ),
    (
        "DESC:cosmodc2:photometry",
        "1.1.0",
        "cosmodc2-team",
        "group",
        "CosmoDC2 mock photometry catalog with updated extinction model.",
        "dummy",
        ["simulation", "dc2"],
    ),
    (
        "DESC:cosmodc2:photometry",
        "2.0.0",
        "cosmodc2-team",
        "group",
        "CosmoDC2 v2 photometry, recalibrated zero-points.",
        "dummy",
        ["simulation", "dc2", "calibrated"],
    ),
    (
        "DESC:srd:y1-shear-cl",
        "0.3.0",
        "srd-wg",
        "project",
        "Year 1 cosmic shear C_ell measurements.",
        "dummy",
        ["observation", "shear"],
    ),
    (
        "DESC:srd:y1-shear-cl",
        "0.3.1",
        "srd-wg",
        "project",
        "Y1 cosmic shear C_ell, patch release for masking bug.",
        "dummy",
        ["observation", "shear"],
    ),
    (
        "DESC:dc2:object-catalog",
        "1.0.0",
        "dc2-validation",
        "group",
        "DC2 object catalog from coadd processing.",
        "dummy",
        ["dc2", "observation"],
    ),
    (
        "DESC:dc2:truth-match",
        "1.0.0",
        "dc2-validation",
        "group",
        "Truth-to-object match table for DC2.",
        "dummy",
        ["dc2", "truth-match"],
    ),
    (
        "DESC:tutorials:sample-lightcurves",
        "1.0.0",
        None,  # falls back to $USER
        "user",
        "Tiny lightcurve sample used in onboarding tutorials.",
        "dummy",
        ["tutorial"],
    ),
    (
        "DESC:external:gaia-dr3-subset",
        "3.0.0",
        "external-data",
        "group",
        "Subset of Gaia DR3 used as astrometric reference.",
        "external",
        ["observation", "astrometry"],
    ),
    (
        "DESC:meta:pipeline-run-log-2026Q1",
        "1.0.0",
        "ops",
        "group",
        "Metadata-only record of the Q1 2026 pipeline run.",
        "meta_only",
        ["ops"],
    ),
    (
        "DESC:legacy:old-mock",
        "0.9.0",
        "legacy",
        "group",
        "Older mock retained for regression checks.",
        "dummy",
        ["deprecated", "simulation"],
    ),
]


# A few datasets to register in the production schema as well, so queries that
# span both schemas have something to find.
DATASETS_PRODUCTION = [
    # For production entries, `owner` must equal "production" (enforced by the
    # registrar).
    (
        "DESC:prod:reference-catalog",
        "1.0.0",
        "production",
        "production",
        "Frozen production reference catalog.",
        "dummy",
        ["observation"],
    ),
    (
        "DESC:prod:reference-catalog",
        "1.1.0",
        "production",
        "production",
        "Reference catalog with bug-fix for proper motions.",
        "dummy",
        ["observation"],
    ),
    (
        "DESC:prod:dc2-coadd",
        "2.0.0",
        "production",
        "production",
        "Production DC2 coadd images, v2 reprocessing.",
        "dummy",
        ["dc2", "simulation"],
    ),
]


# Custom (non-preset) keywords to add on top of the system keywords created by
# create_registry_schema.py.
CUSTOM_KEYWORDS = [
    "shear",
    "truth-match",
    "calibrated",
    "tutorial",
    "astrometry",
    "ops",
]


# Aliases — pairs of (alias_name, dataset_name, version) pointing at a working
# dataset. Lets users test alias lookup.
ALIASES = [
    ("cosmodc2-photometry-latest", "DESC:cosmodc2:photometry", "2.0.0"),
    ("y1-shear-cl-stable", "DESC:srd:y1-shear-cl", "0.3.1"),
    ("dc2-object-catalog", "DESC:dc2:object-catalog", "1.0.0"),
]


# Dependencies — each pair means "register dataset B with an execution whose
# input_datasets is [A]". Creates rows in the `dependency` table.
DEPENDENCIES = [
    ("DESC:cosmodc2:photometry@1.0.0", "DESC:cosmodc2:photometry@1.1.0"),
    ("DESC:cosmodc2:photometry@1.1.0", "DESC:cosmodc2:photometry@2.0.0"),
    ("DESC:dc2:object-catalog@1.0.0", "DESC:dc2:truth-match@1.0.0"),
    ("DESC:srd:y1-shear-cl@0.3.0", "DESC:srd:y1-shear-cl@0.3.1"),
]


def _ensure_root_dir(root_dir, schemas):
    """Create the dummy root_dir layout expected by the registrar."""
    for schema in schemas:
        for owner_type in ("user", "group", "project", "production"):
            os.makedirs(os.path.join(root_dir, schema, owner_type), exist_ok=True)


def _register_dataset(datareg, name, version, owner, owner_type, description,
                      location_type, keywords, input_datasets=None,
                      execution_name=None):
    kwargs = dict(
        name=name,
        version=version,
        owner=owner,
        owner_type=owner_type,
        description=description,
        location_type=location_type,
        keywords=keywords,
        creation_date=None,
    )
    if input_datasets:
        kwargs["input_datasets"] = input_datasets
    if execution_name:
        kwargs["execution_name"] = execution_name
    # `external` datasets need a contact or url to satisfy validation.
    if location_type == "external":
        kwargs["url"] = "https://example.org/data/{}".format(name.replace(":", "_"))
        kwargs["contact_email"] = "dataops@example.org"

    dataset_id, execution_id = datareg.registrar.dataset.register(**kwargs)
    return dataset_id, execution_id


def seed_schema(root_dir, namespace, entry_mode, datasets, dependencies,
                aliases, custom_keywords, logging_level):
    """Seed a single schema (working or production) and return the
    {(name, version): dataset_id} map for cross-references."""

    datareg = DataRegistry(
        root_dir=root_dir,
        namespace=namespace,
        entry_mode=entry_mode,
        logging_level=logging_level,
    )

    # Add custom keywords (only needed once per namespace — they live in the
    # working schema). Wrap in a try so a rerun doesn't blow up on the unique
    # constraint.
    if entry_mode == "working":
        try:
            datareg.registrar.keyword.create_keywords(custom_keywords)
            print(f"  added {len(custom_keywords)} custom keywords")
        except Exception as exc:
            print(f"  keywords already present, skipping ({exc.__class__.__name__})")

    # Register datasets without dependencies first so we can resolve IDs when
    # registering the dependent ones below.
    id_map = {}
    for name, version, owner, owner_type, desc, loc, kws in datasets:
        try:
            d_id, _ = _register_dataset(
                datareg, name, version, owner, owner_type, desc, loc, kws,
            )
            id_map[(name, version)] = d_id
            print(f"  registered {name}@{version} -> id {d_id}")
        except Exception as exc:
            # Most likely a rerun where the entry already exists. Keep going.
            print(f"  skipped {name}@{version} ({exc.__class__.__name__}: {exc})")

    # Dependencies: re-register the downstream dataset under a fresh execution
    # name so the registrar links the upstream as an input. We only do this
    # when the downstream was just created (otherwise we'd hit a uniqueness
    # error). For the seed, we instead just create extra executions and link
    # them via the execution registrar to avoid duplicating datasets.
    if dependencies:
        for upstream, downstream in dependencies:
            up_name, up_ver = upstream.split("@")
            down_name, down_ver = downstream.split("@")
            up_id = id_map.get((up_name, up_ver))
            down_id = id_map.get((down_name, down_ver))
            if up_id is None or down_id is None:
                print(f"  dependency skipped ({upstream} -> {downstream}): "
                      f"missing dataset id")
                continue
            try:
                ex_id = datareg.registrar.execution.register(
                    name=f"derive {down_name}@{down_ver} from {up_name}@{up_ver}",
                    description=("Derivation execution recording that "
                                 f"{down_name}@{down_ver} was produced from "
                                 f"{up_name}@{up_ver}."),
                    site="dev-laptop",
                    input_datasets=[up_id],
                )
                print(f"  dependency execution {ex_id}: {upstream} -> {downstream}")
            except Exception as exc:
                print(f"  dependency execution failed "
                      f"({upstream} -> {downstream}): {exc.__class__.__name__}")

    # Aliases (working only)
    if aliases and entry_mode == "working":
        for alias_name, ds_name, ds_ver in aliases:
            d_id = id_map.get((ds_name, ds_ver))
            if d_id is None:
                print(f"  alias skipped ({alias_name}): no dataset id for "
                      f"{ds_name}@{ds_ver}")
                continue
            try:
                a_id = datareg.registrar.dataset_alias.register(
                    aliasname=alias_name, dataset_id=d_id,
                )
                print(f"  alias {alias_name} -> {ds_name}@{ds_ver} (id {a_id})")
            except Exception as exc:
                print(f"  alias {alias_name} failed: {exc.__class__.__name__}")

    return id_map


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root-dir", required=True,
                        help="Filesystem root_dir for registered datasets.")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE,
                        help=f"Namespace to seed (default {DEFAULT_NAMESPACE}).")
    parser.add_argument("--verbose", action="store_true",
                        help="Verbose (DEBUG) logging from DataRegistry.")
    args = parser.parse_args()

    logging_level = logging.DEBUG if args.verbose else logging.WARNING

    # Both schemas need their owner-type subdirs to exist on disk before the
    # registrar will accept dataset entries.
    _ensure_root_dir(args.root_dir,
                     [f"{args.namespace}_working", f"{args.namespace}_production"])

    print(f"Seeding working schema ({args.namespace}_working)...")
    seed_schema(
        root_dir=args.root_dir,
        namespace=args.namespace,
        entry_mode="working",
        datasets=DATASETS_WORKING,
        dependencies=DEPENDENCIES,
        aliases=ALIASES,
        custom_keywords=CUSTOM_KEYWORDS,
        logging_level=logging_level,
    )

    print(f"Seeding production schema ({args.namespace}_production)...")
    seed_schema(
        root_dir=args.root_dir,
        namespace=args.namespace,
        entry_mode="production",
        datasets=DATASETS_PRODUCTION,
        dependencies=[],
        aliases=[],
        custom_keywords=[],
        logging_level=logging_level,
    )

    print("Seed complete.")


if __name__ == "__main__":
    main()
