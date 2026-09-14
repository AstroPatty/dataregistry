"""A small, fixed corpus of registry rows for the ``/datasets/query`` tests.

The rows are inserted with plain ``INSERT`` statements rather than through
``DataRegistry``/``Registrar`` because the registrar opens and commits on its
own connection, which would escape the per-test transaction the ``connection``
fixture rolls back. Inserting on the test connection keeps every test isolated.

The corpus is deliberately small and hand-tuned so that each query in the test
suite has an unambiguous expected answer, and so that several near-miss rows
exist to catch over-matching (see ``DESC5``/``DESC6``, which would be matched
by a wildcard query whose ``_``/``%`` escaping is broken).
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Connection, text

#: Bit positions of the dataset `status` bitmask, mirroring
#: `dataregistry.registrar.dataset_util.VALID_STATUS_BITS`.
STATUS_VALID = 1 << 0
STATUS_DELETED = 1 << 1
STATUS_ARCHIVED = 1 << 2
STATUS_REPLACED = 1 << 3

CREATOR_UID = "apitest"
ROOT_DIR = "/tmp/dataregistry-api-test"


@dataclass(frozen=True)
class SeedDataset:
    """One row of the ``dataset`` table, plus the keywords attached to it."""

    key: str
    name: str
    version_string: str
    owner: str
    owner_type: str
    description: str
    location_type: str
    nfiles: int
    status: int
    register_date: datetime
    access_api: str | None = None
    total_disk_space: float | None = None
    keywords: tuple[str, ...] = ()
    execution: str | None = None

    @property
    def version_parts(self) -> tuple[int, int, int]:
        major, minor, patch = self.version_string.split(".")
        return int(major), int(minor), int(patch)


@dataclass(frozen=True)
class SeedExecution:
    key: str
    name: str
    description: str
    site: str


def _at(day: int) -> datetime:
    """A distinct, ordered timestamp per row so `order_by` is deterministic."""
    return datetime(2026, 1, day, 12, 0, 0)


EXECUTIONS: tuple[SeedExecution, ...] = (
    SeedExecution(
        key="photometry",
        name="photometry-pipeline",
        description="Nightly photometry reduction.",
        site="NERSC",
    ),
    SeedExecution(
        key="truth",
        name="truth-pipeline",
        description="Truth table matching.",
        site="ALCF",
    ),
)

#: Datasets seeded into the *working* schema.
#:
#: Notable properties the tests rely on:
#:   - `DESC1`/`DESC2` share a name and differ only by version.
#:   - `DESC5` is the only lowercase `desc:` name (case-sensitivity probes).
#:   - `DESC3` holds a literal `%` and `DESC6` a near-miss without it.
#:   - `DESC4` holds a literal `_` and `DESC5` a near-miss with `Z` instead.
#:   - `DESC3`, `DESC5`, `DESC6` have no execution (inner-join probes).
#:   - `nfiles` is unique and ascending, making it a stable sort key.
WORKING_DATASETS: tuple[SeedDataset, ...] = (
    SeedDataset(
        key="DESC1",
        name="DESC:sky:photometry",
        version_string="1.0.0",
        owner="alice",
        owner_type="user",
        description="Baseline sky photometry.",
        location_type="dummy",
        nfiles=10,
        status=STATUS_VALID,
        register_date=_at(1),
        access_api="GCRCatalogs",
        total_disk_space=100.5,
        keywords=("simulation",),
        execution="photometry",
    ),
    SeedDataset(
        key="DESC2",
        name="DESC:sky:photometry",
        version_string="2.0.0",
        owner="alice",
        owner_type="user",
        description="Recalibrated sky photometry.",
        location_type="dummy",
        nfiles=20,
        status=STATUS_VALID,
        register_date=_at(2),
        access_api="GCRCatalogs",
        total_disk_space=250.0,
        keywords=("simulation",),
        execution="photometry",
    ),
    SeedDataset(
        key="DESC3",
        name="DESC:sky:shear",
        version_string="2.1.3",
        owner="alice",
        owner_type="user",
        description="Shear catalog at 50% completeness.",
        location_type="dummy",
        nfiles=30,
        status=STATUS_VALID | STATUS_REPLACED,
        register_date=_at(3),
        total_disk_space=75.25,
    ),
    SeedDataset(
        key="DESC4",
        name="DESC:truth:catalog",
        version_string="1.0.0",
        owner="bob",
        owner_type="group",
        description="Truth catalog with snake_case columns.",
        location_type="dummy",
        nfiles=40,
        status=STATUS_VALID,
        register_date=_at(4),
        access_api="skyCatalogs",
        total_disk_space=10.0,
        keywords=("observation",),
        execution="truth",
    ),
    SeedDataset(
        key="DESC5",
        name="desc:lowercase:name",
        version_string="1.0.0",
        owner="bob",
        owner_type="group",
        description="Lowercase name with snakeZcase columns.",
        location_type="external",
        nfiles=50,
        status=STATUS_VALID,
        register_date=_at(5),
    ),
    SeedDataset(
        key="DESC6",
        name="DESC:legacy:removed",
        version_string="0.1.0",
        owner="carol",
        owner_type="project",
        description="Legacy dataset at 50 percent completeness, removed from disk.",
        location_type="dummy",
        nfiles=60,
        status=STATUS_VALID | STATUS_DELETED,
        register_date=_at(6),
    ),
)

#: Datasets seeded into the *production* schema.
PRODUCTION_DATASETS: tuple[SeedDataset, ...] = (
    SeedDataset(
        key="PROD1",
        name="DESC:prod:reference",
        version_string="1.0.0",
        owner="prod-team",
        owner_type="production",
        description="Production reference catalog.",
        location_type="dummy",
        nfiles=70,
        status=STATUS_VALID,
        register_date=_at(7),
        total_disk_space=500.0,
    ),
)

#: `dependency` rows, as (input dataset key, execution key).
DEPENDENCIES: tuple[tuple[str, str], ...] = (("DESC1", "photometry"),)


@dataclass
class SeededRegistry:
    """Ids assigned to the seeded rows, keyed by their corpus key."""

    dataset_ids: dict[str, int] = field(default_factory=dict)
    execution_ids: dict[str, int] = field(default_factory=dict)

    def dataset(self, key: str) -> SeedDataset:
        for candidate in WORKING_DATASETS + PRODUCTION_DATASETS:
            if candidate.key == key:
                return candidate
        raise KeyError(key)


_INSERT_EXECUTION = text(
    """
    INSERT INTO {schema}.execution
        (name, description, site, register_date, creator_uid)
    VALUES
        (:name, :description, :site, :register_date, :creator_uid)
    RETURNING execution_id
    """
)

_INSERT_DATASET = text(
    """
    INSERT INTO {schema}.dataset
        (name, version_string, version_major, version_minor, version_patch,
         owner, owner_type, description, location_type, nfiles,
         total_disk_space, access_api, status, register_date, creator_uid,
         register_root_dir, is_overwritable, replace_iteration, execution_id)
    VALUES
        (:name, :version_string, :version_major, :version_minor,
         :version_patch, :owner, :owner_type, :description, :location_type,
         :nfiles, :total_disk_space, :access_api, :status, :register_date,
         :creator_uid, :register_root_dir, false, 0, :execution_id)
    RETURNING dataset_id
    """
)


def _insert_executions(
    connection: Connection, schema: str, seeded: SeededRegistry
) -> None:
    statement = text(_INSERT_EXECUTION.text.format(schema=schema))
    for execution in EXECUTIONS:
        execution_id = connection.execute(
            statement,
            {
                "name": execution.name,
                "description": execution.description,
                "site": execution.site,
                "register_date": _at(1),
                "creator_uid": CREATOR_UID,
            },
        ).scalar_one()
        seeded.execution_ids[execution.key] = execution_id


def _insert_datasets(
    connection: Connection,
    schema: str,
    datasets: tuple[SeedDataset, ...],
    seeded: SeededRegistry,
) -> None:
    statement = text(_INSERT_DATASET.text.format(schema=schema))
    for dataset in datasets:
        major, minor, patch = dataset.version_parts
        execution_id = (
            seeded.execution_ids[dataset.execution] if dataset.execution else None
        )
        dataset_id = connection.execute(
            statement,
            {
                "name": dataset.name,
                "version_string": dataset.version_string,
                "version_major": major,
                "version_minor": minor,
                "version_patch": patch,
                "owner": dataset.owner,
                "owner_type": dataset.owner_type,
                "description": dataset.description,
                "location_type": dataset.location_type,
                "nfiles": dataset.nfiles,
                "total_disk_space": dataset.total_disk_space,
                "access_api": dataset.access_api,
                "status": dataset.status,
                "register_date": dataset.register_date,
                "creator_uid": CREATOR_UID,
                "register_root_dir": ROOT_DIR,
                "execution_id": execution_id,
            },
        ).scalar_one()
        seeded.dataset_ids[dataset.key] = dataset_id


def _attach_keywords(
    connection: Connection,
    schema: str,
    datasets: tuple[SeedDataset, ...],
    seeded: SeededRegistry,
) -> None:
    """Link datasets to the preset system keywords created with the schema."""
    lookup = text(f"SELECT keyword_id FROM {schema}.keyword WHERE keyword = :keyword")
    link = text(
        f"""
        INSERT INTO {schema}.dataset_keyword (dataset_id, keyword_id)
        VALUES (:dataset_id, :keyword_id)
        """
    )
    for dataset in datasets:
        for keyword in dataset.keywords:
            keyword_id = connection.execute(lookup, {"keyword": keyword}).scalar_one()
            connection.execute(
                link,
                {
                    "dataset_id": seeded.dataset_ids[dataset.key],
                    "keyword_id": keyword_id,
                },
            )


def _insert_dependencies(
    connection: Connection, schema: str, seeded: SeededRegistry
) -> None:
    statement = text(
        f"""
        INSERT INTO {schema}.dependency
            (input_id, execution_id, register_date)
        VALUES (:input_id, :execution_id, :register_date)
        """
    )
    for dataset_key, execution_key in DEPENDENCIES:
        connection.execute(
            statement,
            {
                "input_id": seeded.dataset_ids[dataset_key],
                "execution_id": seeded.execution_ids[execution_key],
                "register_date": _at(1),
            },
        )


def seed_registry(
    connection: Connection, working_schema: str, production_schema: str
) -> SeededRegistry:
    """Insert the whole corpus and return the ids it was given."""
    seeded = SeededRegistry()
    _insert_executions(connection, working_schema, seeded)
    _insert_datasets(connection, working_schema, WORKING_DATASETS, seeded)
    _insert_datasets(connection, production_schema, PRODUCTION_DATASETS, seeded)
    _attach_keywords(connection, working_schema, WORKING_DATASETS, seeded)
    _insert_dependencies(connection, working_schema, seeded)
    return seeded
