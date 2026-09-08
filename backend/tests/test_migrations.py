"""Migrations must run cleanly in both directions on an empty database.

Estos tests existen por dos bugs reales que se colaron a produccion:

  - Un indice declarado sobre una columna generada que el modelo ORM no
    conocia. Fallaba al importar el modulo de modelos.
  - Un tipo ENUM creado dos veces, porque SQLAlchemy lo crea solo al ver la
    columna dentro de create_table.

Los dos habrian muerto aca en lugar de en el primer despliegue.
"""

from __future__ import annotations

from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from tests.conftest import database_url

EXPECTED_TABLES = {
    "documents",
    "document_pages",
    "chunks",
    "invoices",
    "extracted_fields",
    "llm_calls",
}


def alembic_config() -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url())
    return config


def test_upgrade_creates_every_table(clean_database):
    command.upgrade(alembic_config(), "head")

    tables = set(inspect(clean_database).get_table_names())
    missing = EXPECTED_TABLES - tables
    assert not missing, f"Faltan tablas tras migrar: {missing}"


def test_upgrade_then_downgrade_leaves_no_residue(clean_database):
    """El downgrade tiene que limpiar tambien los tipos, no solo las tablas.

    Un ENUM huerfano hace fallar el siguiente upgrade con DuplicateObject,
    que es exactamente el bug que rompio el primer despliegue.
    """
    config = alembic_config()
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    tables = set(inspect(clean_database).get_table_names())
    assert not (EXPECTED_TABLES & tables), f"Quedaron tablas tras downgrade: {tables}"

    with clean_database.connect() as connection:
        leftover = connection.execute(
            text("SELECT typname FROM pg_type WHERE typname = 'document_status'")
        ).fetchall()
    assert not leftover, "El tipo document_status sobrevivio al downgrade"


def test_upgrade_is_repeatable_after_downgrade(clean_database):
    """Migrar, revertir y volver a migrar sin errores."""
    config = alembic_config()
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    tables = set(inspect(clean_database).get_table_names())
    assert EXPECTED_TABLES <= tables
