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

COPY requirements.txt requirements-dev.txt ./

# En desarrollo instalamos tambien pytest y ruff para poder correr los tests
# dentro del contenedor. La imagen de produccion se construye con
# INSTALL_DEV=false y queda sin esas dependencias.
ARG INSTALL_DEV=true
RUN if [ "$INSTALL_DEV" = "true" ]; then \
      pip install -r requirements-dev.txt; \
    else \
      pip install -r requirements.txt; \
    fi

COPY . .

RUN chmod +x docker-entrypoint.sh

EXPOSE 8000
CMD ["./docker-entrypoint.sh"]
