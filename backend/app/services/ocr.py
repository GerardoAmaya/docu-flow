"""OCR pipeline: rasterize pages, preprocess, and run Tesseract with word-level
bounding boxes and confidence scores."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
import pytesseract
from PIL import Image

from app.core.config import settings

logger = logging.getLogger(__name__)

# Tesseract devuelve -1 en palabras que no pudo puntuar (separadores de bloque).
NO_CONFIDENCE = -1


@dataclass
class OcrWord:
    text: str
    confidence: float  # 0-100, como lo reporta Tesseract
    # Coordenadas normalizadas 0-1 respecto al tamano de la pagina. Guardarlas
    # asi permite que el frontend las escale a cualquier zoom sin recalcular.
    x: float
    y: float
    w: float
    h: float

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "conf": round(self.confidence, 2),
            "x": round(self.x, 5),
            "y": round(self.y, 5),
            "w": round(self.w, 5),
            "h": round(self.h, 5),
        }


@dataclass
class OcrPage:
    page_number: int
    text: str
    confidence: float
    width_px: int
    height_px: int
    image_path: str
    words: list[OcrWord] = field(default_factory=list)


def rasterize(source_path: str, output_dir: Path) -> list[tuple[int, Path, Image.Image]]:
    """Convierte el documento en una imagen PIL por pagina.

    Para PDFs usamos pypdfium2 en vez de pdf2image: renderiza en proceso,
    sin lanzar un binario externo por pagina, y es varias veces mas rapido.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path = Path(source_path)
    results: list[tuple[int, Path, Image.Image]] = []

    if path.suffix.lower() == ".pdf":
        pdf = pdfium.PdfDocument(str(path))
        try:
            # pypdfium2 mide en puntos (72 por pulgada); scale convierte a DPI.
            scale = settings.ocr_dpi / 72
            for index in range(len(pdf)):
                image = pdf[index].render(scale=scale).to_pil().convert("RGB")
                image_path = output_dir / f"p{index + 1:04d}.jpg"
                image.save(image_path, "JPEG", quality=85, optimize=True)
                results.append((index + 1, image_path, image))
        finally:
            pdf.close()
    else:
        image = Image.open(path).convert("RGB")
        image_path = output_dir / "p0001.jpg"
        image.save(image_path, "JPEG", quality=85, optimize=True)
        results.append((1, image_path, image))

    return results


def preprocess(image: Image.Image) -> Image.Image:
    """Limpia la imagen antes del OCR.

    Una foto de recibo tomada con celular tiene sombras, iluminacion despareja
    y ruido. El umbral adaptativo binariza por regiones en vez de usar un solo
    corte global, que es lo que salva las zonas oscuras. En PDFs nativos esto
    casi no cambia nada; en fotos sube la precision de forma notoria.
    """
    array = np.array(image)
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)

    # Elimina ruido puntual conservando los bordes de las letras.
    denoised = cv2.bilateralFilter(gray, d=5, sigmaColor=50, sigmaSpace=50)

    binary = cv2.adaptiveThreshold(
        denoised,
        maxValue=255,
        adaptiveMethod=cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        thresholdType=cv2.THRESH_BINARY,
        blockSize=31,
        C=15,
    )
    return Image.fromarray(binary)


def run_tesseract(image: Image.Image) -> tuple[str, float, list[OcrWord]]:
    """Corre Tesseract y devuelve texto, confianza media y palabras con bbox."""
    width, height = image.size
    data = pytesseract.image_to_data(
        image,
        lang=settings.ocr_languages,
        output_type=pytesseract.Output.DICT,
    )

    words: list[OcrWord] = []
    lines: dict[tuple[int, int, int], list[str]] = {}

    for i, raw_text in enumerate(data["text"]):
        text = raw_text.strip()
        if not text:
            continue
        confidence = float(data["conf"][i])
        if confidence == NO_CONFIDENCE:
            continue

        words.append(
            OcrWord(
                text=text,
                confidence=confidence,
                x=data["left"][i] / width,
                y=data["top"][i] / height,
                w=data["width"][i] / width,
                h=data["height"][i] / height,
            )
        )
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(text)

    full_text = "\n".join(" ".join(tokens) for _, tokens in sorted(lines.items()))

    # Promedio ponderado por longitud: una palabra de diez caracteres pesa mas
    # que un guion suelto. El promedio simple infla la confianza cuando la
    # pagina tiene mucho ruido corto reconocido "bien".
    total_chars = sum(len(word.text) for word in words)
    if total_chars:
        confidence = sum(w.confidence * len(w.text) for w in words) / total_chars
    else:
        confidence = 0.0

    return full_text, confidence, words


def ocr_document(source_path: str, document_id: str) -> list[OcrPage]:
    """Procesa un documento completo y devuelve una entrada por pagina."""
    output_dir = Path(settings.page_image_dir) / str(document_id)
    pages: list[OcrPage] = []

    for page_number, image_path, image in rasterize(source_path, output_dir):
        cleaned = preprocess(image)
        text, confidence, words = run_tesseract(cleaned)

        if confidence < settings.ocr_min_confidence:
            logger.warning(
                "Low OCR confidence on document %s page %s: %.1f",
                document_id,
                page_number,
                confidence,
            )

        pages.append(
            OcrPage(
                page_number=page_number,
                text=text,
                confidence=confidence,
                width_px=image.width,
                height_px=image.height,
                image_path=str(image_path),
                words=words,
            )
        )

    return pages
