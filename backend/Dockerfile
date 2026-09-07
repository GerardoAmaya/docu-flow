FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-spa \
        tesseract-ocr-eng \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN chmod +x docker-entrypoint.sh

# Precarga los pesos del modelo de embeddings en la imagen. Sin esto, el
# primer request en produccion espera una descarga de ~450 MB.
ARG PRELOAD_EMBEDDINGS=false
RUN if [ "$PRELOAD_EMBEDDINGS" = "true" ]; then \
      python -c "from sentence_transformers import SentenceTransformer; \
                 SentenceTransformer('intfloat/multilingual-e5-base')"; \
    fi

EXPOSE 8000
CMD ["./docker-entrypoint.sh"]
