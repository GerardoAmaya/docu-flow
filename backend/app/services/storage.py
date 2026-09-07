"""File storage with content-addressed paths and streaming hash computation."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.core.config import settings

# Firmas binarias de los formatos aceptados. No confiamos en el content-type
# que manda el cliente: un .exe renombrado a .pdf llega con content-type de PDF.
MAGIC_SIGNATURES: dict[bytes, str] = {
    b"%PDF": "application/pdf",
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"II*\x00": "image/tiff",
    b"MM\x00*": "image/tiff",
}

EXTENSION_BY_MIME: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tiff",
}

CHUNK_SIZE = 1024 * 1024  # 1 MiB


class UnsupportedFileType(Exception):
    pass


class FileTooLarge(Exception):
    pass


@dataclass(frozen=True)
class StoredFile:
    content_hash: str
    storage_path: str
    size_bytes: int
    mime_type: str


def detect_mime_type(header: bytes) -> str:
    """Determina el tipo real leyendo los primeros bytes del archivo."""
    for signature, mime in MAGIC_SIGNATURES.items():
        if header.startswith(signature):
            return mime
    raise UnsupportedFileType(
        "Unsupported file type. Accepted: PDF, JPEG, PNG, TIFF."
    )


def _shard_path(content_hash: str, extension: str) -> Path:
    """Reparte los archivos en subcarpetas por prefijo del hash.

    Sin esto, cien mil facturas terminan en un solo directorio y el
    filesystem se arrastra al listarlo.
    """
    base = Path(settings.storage_dir)
    return base / content_hash[:2] / content_hash[2:4] / f"{content_hash}{extension}"


def store_upload(stream: BinaryIO) -> StoredFile:
    """Guarda el stream a disco calculando sha256 en el mismo paso.

    Escribimos primero a un temporal porque el nombre final depende del hash,
    que solo conocemos cuando terminamos de leer. Nunca cargamos el archivo
    completo en memoria: un PDF escaneado de 200 paginas pesa cientos de MB.
    """
    header = stream.read(16)
    if not header:
        raise UnsupportedFileType("Empty file.")
    mime_type = detect_mime_type(header)

    max_bytes = settings.max_upload_mb * 1024 * 1024
    hasher = hashlib.sha256()
    hasher.update(header)
    size = len(header)

    tmp_dir = Path(settings.storage_dir) / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"upload-{id(stream)}.part"

    try:
        with tmp_path.open("wb") as out:
            out.write(header)
            while chunk := stream.read(CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise FileTooLarge(
                        f"File exceeds the {settings.max_upload_mb} MB limit."
                    )
                hasher.update(chunk)
                out.write(chunk)

        content_hash = hasher.hexdigest()
        final_path = _shard_path(content_hash, EXTENSION_BY_MIME[mime_type])
        final_path.parent.mkdir(parents=True, exist_ok=True)

        # Si ya existe, el contenido es identico por definicion del hash:
        # descartamos el temporal en vez de reescribir.
        if final_path.exists():
            tmp_path.unlink(missing_ok=True)
        else:
            shutil.move(str(tmp_path), str(final_path))

        return StoredFile(
            content_hash=content_hash,
            storage_path=str(final_path),
            size_bytes=size,
            mime_type=mime_type,
        )
    finally:
        tmp_path.unlink(missing_ok=True)
