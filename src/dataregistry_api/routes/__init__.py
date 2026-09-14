from fastapi import FastAPI

from .dataset import DatasetRouter


def register_all_routes(app: FastAPI):
    app.include_router(DatasetRouter)
