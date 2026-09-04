from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from sqlalchemy import text

from dataregistry_api.database import ConnectionDependency, dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    dispose_engine()


APP = FastAPI(lifespan=lifespan)


@APP.get("/health")
def health(connection: ConnectionDependency):
    connection.execute(text("SELECT 1"))
    return {"status": "healthy"}
