"""reduce embedding dimension to fit smaller memory footprint

Revision ID: 0002
Revises: 0001

Cambia la columna embedding de 768 a 384 dimensiones para poder usar
multilingual-e5-small en lugar de e5-base. El modelo chico ocupa alrededor
de una cuarta parte de la memoria, lo que permite correr el servicio en
contenedores de 1 GB.

Los vectores existentes se descartan: no hay conversion posible entre
espacios de embedding distintos. Hay que reprocesar los documentos despues
de aplicar esta migracion.
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

OLD_DIM = 768
NEW_DIM = 384


def _swap_dimension(from_dim: int, to_dim: int) -> None:
    # El indice HNSW esta atado a la dimension de la columna, asi que hay que
    # tirarlo antes de alterarla y reconstruirlo despues.
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")

    # Los vectores viejos no se pueden convertir: son de otro espacio.
    op.execute("UPDATE chunks SET embedding = NULL")

    op.alter_column(
        "chunks",
        "embedding",
        existing_type=Vector(from_dim),
        type_=Vector(to_dim),
        existing_nullable=True,
        postgresql_using="NULL",
    )

    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def upgrade() -> None:
    _swap_dimension(OLD_DIM, NEW_DIM)


def downgrade() -> None:
    _swap_dimension(NEW_DIM, OLD_DIM)
