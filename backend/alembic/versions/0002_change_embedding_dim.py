"""cambiar a Voyage AI embeddings: Vector(1024)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-07
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

VOYAGE_EMBEDDING_DIM = 1024
OLD_EMBEDDING_DIM = 768


def upgrade() -> None:
    # 1. Drop el indice HNSW (depende de la columna)
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    
    # 2. Drop la columna embedding
    op.drop_column("chunks", "embedding")
    
    # 3. Crear la nueva columna con dimensión 1024 (Voyage 4-lite)
    op.add_column(
        "chunks",
        sa.Column("embedding", Vector(VOYAGE_EMBEDDING_DIM), nullable=True)
    )
    
    # 4. Recrear el índice HNSW
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    # 1. Drop el indice HNSW
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    
    # 2. Drop la columna embedding
    op.drop_column("chunks", "embedding")
    
    # 3. Restaurar a Vector(768)
    op.add_column(
        "chunks",
        sa.Column("embedding", Vector(OLD_EMBEDDING_DIM), nullable=True)
    )
    
    # 4. Recrear el índice HNSW
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

