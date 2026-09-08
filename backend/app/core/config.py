from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "DocuFlow"
    environment: str = "development"

    # Origenes permitidos, separados por coma. En produccion hay que poner el
    # dominio real del frontend: con localhost cableado, el navegador bloquea
    # todas las llamadas desde el sitio desplegado.
    cors_origins: str = "http://localhost:3000"

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
    # "voyage" llama a la API y no necesita torch en el contenedor.
    # "local" usa sentence-transformers: sin costo por token, pero ~760 MB
    # residentes y torch instalado.
    embedding_provider: str = "voyage"
    voyage_api_key: str | None = None
    voyage_model: str = "voyage-4-lite"
    # Segundos minimos entre llamadas a Voyage. La capa gratuita permite 3 por
    # minuto, asi que 21 deja margen. Poner 0 para desactivar el espaciado.
    voyage_min_interval_seconds: float = 21.0
    embedding_dim: int = 1024

    # Cuantas consultas distintas se recuerdan por proceso. Cada vector de
    # 1024 dimensiones ocupa unos 8 KB, asi que 256 entradas son ~2 MB.
    query_cache_size: int = 256

    # Solo aplican con embedding_provider="local".
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_batch_size: int = 4
    chunk_size_chars: int = 900
    chunk_overlap_chars: int = 150

    # --- OCR ---
    ocr_languages: str = "spa+eng"
    ocr_dpi: int = 300
    # Debajo de esta confianza media, la página se marca para revisión humana.
    ocr_min_confidence: float = 70.0

    # Un campo extraído por debajo de este umbral entra a la cola de revisión.
    field_review_threshold: float = 0.85

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
