from typing import Annotated

from fastapi import APIRouter, Depends, Query

from dataregistry_api.auth import require_globus_principal
from dataregistry_api.database import EngineDependency
from dataregistry_api.handlers import find_datasets
from dataregistry_api.models import (
    DatasetQueryParameters,
    DatasetQueryRequest,
    DatasetQueryResponse,
)

DatasetRouter = APIRouter(
    prefix="/datasets",
    tags=["Datasets"],
    dependencies=[Depends(require_globus_principal)],
)

QueryParameterDependency = Annotated[DatasetQueryParameters, Query()]


@DatasetRouter.post("/query")
def query_datasets(
    engine: EngineDependency,
    parameters: QueryParameterDependency,
    request: DatasetQueryRequest,
) -> DatasetQueryResponse:
    """Query datasets, returning the rows that match every filter."""
    return find_datasets(engine, parameters, request)
