"""esquema inicial

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EMBEDDING_DIM = 768


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # create_type=False evita que SQLAlchemy intente crear el tipo otra vez
    # cuando encuentre la columna dentro de create_table. Lo creamos nosotros.
    document_status = postgresql.ENUM(
        "pending", "ocr_running", "extracting", "embedding",
        "needs_review", "completed", "failed",
        name="document_status",
        create_type=False,
    )
    document_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("status", document_status, nullable=False, server_default="pending"),
        sa.Column("page_count", sa.Integer),
        sa.Column("error_message", sa.Text),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_documents_status", "documents", ["status"])

    op.create_table(
        "document_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer, nullable=False),
        sa.Column("image_path", sa.String(1024)),
        sa.Column("width_px", sa.Integer),
        sa.Column("height_px", sa.Integer),
        sa.Column("ocr_text", sa.Text),
        sa.Column("ocr_confidence", sa.Float),
        sa.Column("ocr_words", postgresql.JSONB),
        sa.UniqueConstraint("document_id", "page_number"),
    )
    op.create_index("ix_document_pages_document_id", "document_pages", ["document_id"])

    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("document_pages.id", ondelete="SET NULL")),
        sa.Column("page_number", sa.Integer),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.UniqueConstraint("document_id", "chunk_index"),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])

    # Columna generada: Postgres mantiene el tsvector sincronizado con content.
    # Español para que el stemming funcione con "facturas" -> "factur".
    op.execute(
        "ALTER TABLE chunks ADD COLUMN tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('spanish', content)) STORED"
    )
    op.execute("CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv)")
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("vendor_name", sa.String(512)),
        sa.Column("vendor_tax_id", sa.String(64)),
        sa.Column("buyer_name", sa.String(512)),
        sa.Column("buyer_tax_id", sa.String(64)),
        sa.Column("invoice_number", sa.String(128)),
        sa.Column("issue_date", sa.Date),
        sa.Column("due_date", sa.Date),
        sa.Column("currency", sa.String(3)),
        sa.Column("subtotal", sa.Numeric(14, 2)),
        sa.Column("tax_amount", sa.Numeric(14, 2)),
        sa.Column("total", sa.Numeric(14, 2)),
        sa.Column("line_items", postgresql.JSONB),
        sa.Column("source_payload", postgresql.JSONB),
        sa.CheckConstraint("total >= 0", name="ck_invoices_total_non_negative"),
    )
    op.create_index("ix_invoices_vendor_name", "invoices", ["vendor_name"])
    op.create_index("ix_invoices_vendor_tax_id", "invoices", ["vendor_tax_id"])
    op.create_index("ix_invoices_invoice_number", "invoices", ["invoice_number"])
    op.create_index("ix_invoices_issue_date", "invoices", ["issue_date"])
    op.create_index("ix_invoices_total", "invoices", ["total"])
    # Búsqueda por nombre de proveedor tolerante a errores de OCR.
    op.execute(
        "CREATE INDEX ix_invoices_vendor_trgm ON invoices "
        "USING gin (vendor_name gin_trgm_ops)"
    )

    op.create_table(
        "extracted_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_name", sa.String(128), nullable=False),
        sa.Column("value_text", sa.Text),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("page_number", sa.Integer),
        sa.Column("bbox", postgresql.JSONB),
        sa.Column("source_snippet", sa.Text),
        sa.Column("needs_review", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("corrected_value", sa.Text),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(256)),
        sa.UniqueConstraint("document_id", "field_name"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_fields_confidence_range"),
    )
    op.create_index("ix_extracted_fields_document_id", "extracted_fields", ["document_id"])
    op.create_index("ix_extracted_fields_needs_review", "extracted_fields", ["needs_review"])

    op.create_table(
        "llm_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("was_mocked", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_llm_calls_document_id", "llm_calls", ["document_id"])
    op.create_index("ix_llm_calls_purpose", "llm_calls", ["purpose"])
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])


def downgrade() -> None:
    op.drop_table("llm_calls")
    op.drop_table("extracted_fields")
    op.drop_table("invoices")
    op.drop_table("chunks")
    op.drop_table("document_pages")
    op.drop_table("documents")
    op.execute("DROP TYPE IF EXISTS document_status")
