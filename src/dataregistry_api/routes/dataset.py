from typing import Annotated

from fastapi import APIRouter, Query

from dataregistry_api.database import ConnectionDependency
from dataregistry_api.handlers import find_datasets
from dataregistry_api.models import (
    DatasetQueryParameters,
    DatasetQueryRequest,
    DatasetQueryResponse,
)

DatasetRouter = APIRouter(prefix="/datasets", tags=["Datasets"])

QueryParameterDependency = Annotated[DatasetQueryParameters, Query()]


@DatasetRouter.post("/query")
def query_datasets(
    connection: ConnectionDependency,
    parameters: QueryParameterDependency,
    request: DatasetQueryRequest,
) -> DatasetQueryResponse:
    """Query datasets, returning the rows that match every filter."""
    return find_datasets(connection, parameters, request)
