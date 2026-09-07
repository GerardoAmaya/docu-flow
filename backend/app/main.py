from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import chat, documents, invoices, pages, search
from app.core.config import settings
from app.core.db import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    yield


app = FastAPI(
    title=settings.app_name,
    description="Extraccion estructurada y busqueda semantica sobre documentos escaneados.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)


app.include_router(documents.router)
app.include_router(pages.router)
app.include_router(invoices.router)
app.include_router(search.router)
app.include_router(chat.router)


@app.get("/health", tags=["infra"])
def health() -> dict:
    """Verifica que la API alcance Postgres y que pgvector este instalado."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        has_vector = conn.execute(
            text("SELECT COUNT(*) FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one()
    return {
        "status": "ok",
        "database": "connected",
        "pgvector": bool(has_vector),
        "mock_llm": settings.mock_llm,
        "embedding_model": settings.embedding_model,
    }
