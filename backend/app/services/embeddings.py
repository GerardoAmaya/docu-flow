"""Text chunking and embeddings with a pluggable provider.

Dos proveedores:

  voyage  (por defecto)  Llama a la API de Voyage. El contenedor no necesita
                         torch, asi que la imagen baja de ~2 GB a ~300 MB y
                         la memoria residente de ~760 MB a ~200 MB.

  local                  sentence-transformers en CPU. Sin costo por token y
                         funciona sin red, pero exige instalar torch y no
                         entra en contenedores de 1 GB.

Se empezo con el modelo local para que el costo no escalara con el volumen.
Al desplegar, la restriccion real resulto ser la memoria y no el costo, asi
que el valor por defecto cambio. La abstraccion deja ambos caminos abiertos.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings

logger = logging.getLogger(__name__)

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
# La API acepta hasta 128 entradas por llamada.
VOYAGE_MAX_BATCH = 128

# Los modelos e5 fueron entrenados con estos prefijos y pierden calidad
# medible sin ellos. Voyage usa input_type para lo mismo.
E5_QUERY_PREFIX = "query: "
E5_PASSAGE_PREFIX = "passage: "

_local_model = None
_lock = threading.Lock()

# Espaciado entre llamadas a la API. La capa gratuita de Voyage permite 3
# peticiones por minuto: con doce documentos compitiendo, el backoff
# exponencial choca y reintenta a ciegas hasta agotarse. Serializar las
# llamadas a un ritmo sostenible convierte un fallo en una espera.
_pace_lock = threading.Lock()
_last_call_at = 0.0


def _wait_for_turn() -> None:
    """Bloquea hasta que haya pasado el intervalo minimo desde la ultima llamada."""
    global _last_call_at
    interval = settings.voyage_min_interval_seconds
    if interval <= 0:
        return
    with _pace_lock:
        elapsed = time.monotonic() - _last_call_at
        if elapsed < interval:
            wait = interval - elapsed
            logger.debug("Esperando %.1fs por el limite de la API", wait)
            time.sleep(wait)
        _last_call_at = time.monotonic()


# Cache de vectores de consulta. Sin tarjeta, Voyage limita a 3 peticiones por
# minuto: cuatro preguntas seguidas en la demo y la cuarta falla. Las consultas
# se repiten mucho mas que los documentos, asi que un cache chico evita casi
# todas las llamadas.
_query_cache: OrderedDict[tuple[str, str, int], list[float]] = OrderedDict()
_cache_lock = threading.Lock()


class EmbeddingError(Exception):
    pass


class TransientEmbeddingError(EmbeddingError):
    """Rate limit o error de red: vale la pena reintentar."""


# --------------------------------------------------------------------------
# Voyage
# --------------------------------------------------------------------------


@retry(
    retry=retry_if_exception_type(TransientEmbeddingError),
    stop=stop_after_attempt(6),
    wait=wait_exponential(multiplier=2, min=5, max=60),
    reraise=True,
)
def _voyage_call(texts: list[str], input_type: str) -> list[list[float]]:
    if not settings.voyage_api_key:
        raise EmbeddingError(
            "VOYAGE_API_KEY esta vacia. Ponela en el .env o usa EMBEDDING_PROVIDER=local."
        )

    payload = {
        "input": texts,
        "model": settings.voyage_model,
        # Voyage vectoriza distinto una consulta que un documento, igual que
        # los prefijos de e5.
        "input_type": input_type,
        "output_dimension": settings.embedding_dim,
    }

    _wait_for_turn()

    request = urllib.request.Request(
        VOYAGE_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.voyage_api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 429 or exc.code >= 500:
            raise TransientEmbeddingError(f"{exc.code}: {detail}") from exc
        raise EmbeddingError(f"{exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise TransientEmbeddingError(str(exc)) from exc

    # La respuesta puede venir desordenada; el campo index dice a que entrada
    # corresponde cada vector.
    items = sorted(body["data"], key=lambda d: d["index"])
    return [item["embedding"] for item in items]


def _voyage_embed(texts: list[str], input_type: str) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), VOYAGE_MAX_BATCH):
        vectors.extend(_voyage_call(texts[start : start + VOYAGE_MAX_BATCH], input_type))
    return vectors


# --------------------------------------------------------------------------
# Modelo local
# --------------------------------------------------------------------------


def _get_local_model():
    """Importa sentence-transformers solo si se usa este proveedor.

    El import esta adentro a proposito: torch no esta en requirements.txt, y
    si estuviera arriba el modulo entero fallaria al importarse en el
    despliegue por defecto.
    """
    global _local_model
    if _local_model is None:
        with _lock:
            if _local_model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise EmbeddingError(
                        "EMBEDDING_PROVIDER=local necesita sentence-transformers "
                        "y torch. Instalalos o usa EMBEDDING_PROVIDER=voyage."
                    ) from exc

                logger.info("Loading local embedding model %s", settings.embedding_model)
                _local_model = SentenceTransformer(settings.embedding_model, device="cpu")
    return _local_model


def _local_embed(texts: list[str], prefix: str) -> list[list[float]]:
    model = _get_local_model()
    vectors = model.encode(
        [prefix + t for t in texts],
        batch_size=settings.embedding_batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [v.tolist() for v in vectors]


# --------------------------------------------------------------------------
# Interfaz publica
# --------------------------------------------------------------------------


def embed_passages(texts: list[str]) -> list[list[float]]:
    """Vectoriza fragmentos para guardar en la base."""
    if not texts:
        return []
    if settings.embedding_provider == "local":
        return _local_embed(texts, E5_PASSAGE_PREFIX)
    return _voyage_embed(texts, "document")


def embed_query(text: str) -> list[float]:
    """Vectoriza una consulta del usuario, con cache LRU en memoria."""
    key = (text.strip().lower(), settings.embedding_provider, settings.embedding_dim)

    with _cache_lock:
        hit = _query_cache.get(key)
        if hit is not None:
            _query_cache.move_to_end(key)
            return hit

    if settings.embedding_provider == "local":
        vector = _local_embed([text], E5_QUERY_PREFIX)[0]
    else:
        vector = _voyage_embed([text], "query")[0]

    with _cache_lock:
        _query_cache[key] = vector
        while len(_query_cache) > settings.query_cache_size:
            _query_cache.popitem(last=False)

    return vector


def chunk_text(text: str, page_number: int) -> list[tuple[int, str]]:
    """Parte el texto en fragmentos con solapamiento.

    Cortamos en limites de linea porque el OCR de una factura ya viene
    estructurado asi: partir a la mitad de "IVA 13%: USD 401.57" haria que ese
    dato no se encuentre por ninguno de los dos lados. El solapamiento cubre
    el caso de un dato que cae justo en el borde.
    """
    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if not text:
        return []

    size = settings.chunk_size_chars
    overlap = settings.chunk_overlap_chars

    if len(text) <= size:
        return [(page_number, text)]

    lines = text.split("\n")
    chunks: list[str] = []
    current: list[str] = []
    length = 0

    for line in lines:
        if length + len(line) + 1 > size and current:
            chunks.append("\n".join(current))
            carried: list[str] = []
            carried_len = 0
            for previous in reversed(current):
                if carried_len + len(previous) > overlap:
                    break
                carried.insert(0, previous)
                carried_len += len(previous) + 1
            current = carried
            length = carried_len
        current.append(line)
        length += len(line) + 1

    if current:
        chunks.append("\n".join(current))

    return [(page_number, c) for c in chunks if c.strip()]
