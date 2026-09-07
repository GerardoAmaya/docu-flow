from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "DocuFlow"
    environment: str = "development"

    database_url: str = "postgresql+psycopg://docuflow:docuflow@db:5432/docuflow"
    redis_url: str = "redis://redis:6379/0"

    storage_dir: str = "/data/uploads"
    page_image_dir: str = "/data/pages"
    max_upload_mb: int = 25

    # --- LLM ---
    # Cuando es True el pipeline corre completo sin gastar tokens ni necesitar API key.
    mock_llm: bool = True
    anthropic_api_key: str | None = None

    # Extraccion: alto volumen, tarea acotada -> modelo barato.
    extraction_model: str = "claude-haiku-4-5-20251001"
    # Respuestas del chat RAG: bajo volumen, requiere razonar -> modelo fuerte.
    answer_model: str = "claude-sonnet-5"
    llm_max_tokens: int = 4096

    # Precio por millon de tokens, por modelo. Consulta claude.com/pricing
    # y ajusta en el .env. Se usa solo para calcular cost_usd en llm_calls.
    price_input_per_mtok: dict[str, float] = {}
    price_output_per_mtok: dict[str, float] = {}

    # --- Embeddings (locales, sin costo por token) ---
    embedding_model: str = "intfloat/multilingual-e5-base"
    embedding_dim: int = 768
    chunk_size_chars: int = 900
    chunk_overlap_chars: int = 150

    # --- OCR ---
    ocr_languages: str = "spa+eng"
    ocr_dpi: int = 300
    # Debajo de esta confianza media, la página se marca para revisión humana.
    ocr_min_confidence: float = 70.0

    # Un campo extraído por debajo de este umbral entra a la cola de revisión.
    field_review_threshold: float = 0.85


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
