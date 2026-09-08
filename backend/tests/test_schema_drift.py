"""The ORM models and the migrated database must not diverge.

El modelo y el esquema pueden separarse sin que nada avise hasta que truena
en tiempo de ejecucion. Este test compara ambos y falla si difieren.
"""

from __future__ import annotations

from sqlalchemy import inspect

from alembic import command
from app import models  # noqa: F401  (registra las tablas en Base.metadata)
from app.core.db import Base
from tests.test_migrations import alembic_config


def test_every_model_table_exists_in_the_database(clean_database):
    command.upgrade(alembic_config(), "head")

    inspector = inspect(clean_database)
    real_tables = set(inspector.get_table_names())

    for name in Base.metadata.tables:
        assert name in real_tables, f"El modelo declara {name} y la migracion no la crea"


def test_every_model_column_exists_in_the_database(clean_database):
    command.upgrade(alembic_config(), "head")

    inspector = inspect(clean_database)
    problems: list[str] = []

    for table_name, table in Base.metadata.tables.items():
        real_columns = {c["name"] for c in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name not in real_columns:
                problems.append(f"{table_name}.{column.name}")

    assert not problems, f"Columnas del modelo que no existen en la base: {problems}"


def test_generated_tsv_column_is_present_and_indexed(clean_database):
    """La columna tsv se crea con SQL crudo; el modelo la declara aparte.

    Es justo el punto donde modelo y esquema se separaron una vez.
    """
    command.upgrade(alembic_config(), "head")

    inspector = inspect(clean_database)
    columns = {c["name"] for c in inspector.get_columns("chunks")}
    assert "tsv" in columns

    indexes = {i["name"] for i in inspector.get_indexes("chunks")}
    assert "ix_chunks_tsv" in indexes
    assert "ix_chunks_embedding_hnsw" in indexes
