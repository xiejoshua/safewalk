"""Safewalk scoring API — FastAPI backend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.network import GraphRouter
from app.routes import router
from app.segment_repository import SegmentRepository
from app.segments import SegmentStore, create_empty_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings

    parquet_path = Path(settings.scored_segments_path)
    try:
        app.state.segment_store = SegmentStore.from_parquet(parquet_path)
        logger.info("Segment store ready (%s)", parquet_path)
    except FileNotFoundError:
        logger.warning(
            "Scored segments not found at %s. Run `python scripts/prebake.py` from the repo root.",
            parquet_path,
        )
        app.state.segment_store = create_empty_store()

    app.state.graph_router = GraphRouter.from_segment_store(app.state.segment_store)
    app.state.segment_repository = SegmentRepository(app.state.segment_store)

    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Safewalk API",
        description="Safe-walk routing scorer for MARTA first/last mile",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    return app


app = create_app()
