from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from sqlalchemy import text

from dataregistry_api.database import ConnectionDependency, dispose_engine
from dataregistry_api.errors import register_error_handlers
from dataregistry_api.routes import register_all_routes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    dispose_engine()


APP = FastAPI(lifespan=lifespan)
register_all_routes(APP)
register_error_handlers(APP)


@APP.get("/health")
def health(connection: ConnectionDependency):
    connection.execute(text("SELECT 1"))
    return {"status": "healthy"}
