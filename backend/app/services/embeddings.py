"""Local embeddings and text chunking.

El modelo corre dentro del contenedor, sin llamadas de red ni costo por token.
A cambio ocupa unos 500 MB de RAM por proceso y el primer uso descarga los
pesos, que quedan cacheados en el volumen hfcache.
"""

from __future__ import annotations

import logging
import re
import threading

from app.core.config import settings

logger = logging.getLogger(__name__)

_model = None
_lock = threading.Lock()

# Los modelos de la familia e5 fueron entrenados con estos prefijos y pierden
# calidad notablemente sin ellos. No son decorativos.
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "


def get_model():
    """Carga el modelo una sola vez por proceso, de forma segura entre hilos."""
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                logger.info("Loading embedding model %s", settings.embedding_model)
                _model = SentenceTransformer(settings.embedding_model, device="cpu")
    return _model


def embed_passages(texts: list[str]) -> list[list[float]]:
    """Vectoriza fragmentos para guardar en la base."""
    if not texts:
        return []
    model = get_model()
    vectors = model.encode(
        [PASSAGE_PREFIX + t for t in texts],
        batch_size=16,
        normalize_embeddings=True,  # permite usar coseno como producto punto
        show_progress_bar=False,
    )
    return [v.tolist() for v in vectors]


def embed_query(text: str) -> list[float]:
    """Vectoriza una consulta del usuario."""
    model = get_model()
    vector = model.encode(
        QUERY_PREFIX + text, normalize_embeddings=True, show_progress_bar=False
    )
    return vector.tolist()


def chunk_text(text: str, page_number: int) -> list[tuple[int, str]]:
    """Parte el texto en fragmentos con solapamiento.

    Cortamos en limites de linea porque el OCR de una factura ya viene
    estructurado por lineas: partir a la mitad de "IVA 13%: USD 401.57" haria
    que ese dato no se encuentre por ninguno de los dos lados. El solapamiento
    cubre el caso de un dato que cae justo en el borde.
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
            # Arrastra las ultimas lineas como solapamiento.
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
