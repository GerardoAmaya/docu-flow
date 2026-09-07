"""switch embedding dimension for the Voyage provider

Revision ID: 0003
Revises: 0002

Pasa la columna embedding de 384 a 1024 dimensiones, que es la salida por
defecto de voyage-4-lite.

Motivo del cambio: el modelo local exigia torch en el contenedor, lo que
significaba ~2 GB de imagen y ~760 MB de memoria residente, por encima del
limite de los planes gratuitos. Con la API el contenedor baja a ~200 MB.

Los vectores existentes se descartan; hay que reprocesar los documentos.
"""
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

OLD_DIM = 384
NEW_DIM = 1024


def _swap_dimension(from_dim: int, to_dim: int) -> None:
    # El indice HNSW esta atado a la dimension, hay que rehacerlo.
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
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
