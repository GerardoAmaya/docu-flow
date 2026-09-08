"""Structured invoice extraction with provenance grounding and validation.

El flujo por campo es:
  1. El modelo devuelve valor + fragmento de origen + su confianza
  2. Buscamos ese fragmento en las palabras del OCR -> bbox y confianza OCR
  3. Aplicamos reglas de validacion (aritmetica, formato, rango)
  4. Combinamos las tres senales; la mas debil manda
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from dateutil import parser as date_parser
from pydantic import BaseModel, Field, field_validator

from app.core.config import settings

logger = logging.getLogger(__name__)

FIELD_NAMES = [
    "vendor_name",
    "vendor_tax_id",
    "buyer_name",
    "buyer_tax_id",
    "invoice_number",
    "issue_date",
    "due_date",
    "currency",
    "subtotal",
    "tax_amount",
    "total",
]

# NIT salvadoreno: 0614-180592-102-3
NIT_PATTERN = re.compile(r"\b\d{4}-\d{6}-\d{3}-\d\b")
MONEY_PATTERN = re.compile(r"\d{1,3}(?:[,\s]\d{3})*(?:\.\d{2})|\d+\.\d{2}")
ISO_CURRENCIES = {"USD", "EUR", "GTQ", "HNL", "CRC", "MXN", "NIO", "PAB"}


# --------------------------------------------------------------------------
# Schema que le exigimos al modelo
# --------------------------------------------------------------------------


class ExtractedValue(BaseModel):
    """Un campo con su procedencia. `source_text` es lo que permite verificarlo."""

    value: str | None = None
    # Fragmento literal del texto OCR de donde salio el valor. Si el modelo
    # no lo puede citar, es senal fuerte de que se lo invento.
    source_text: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("value", "source_text", mode="before")
    @classmethod
    def blank_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


class LineItem(BaseModel):
    description: str
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None


class InvoiceExtraction(BaseModel):
    """Lo que el modelo debe devolver. Todo opcional a proposito."""

    vendor_name: ExtractedValue = ExtractedValue()
    vendor_tax_id: ExtractedValue = ExtractedValue()
    buyer_name: ExtractedValue = ExtractedValue()
    buyer_tax_id: ExtractedValue = ExtractedValue()
    invoice_number: ExtractedValue = ExtractedValue()
    issue_date: ExtractedValue = ExtractedValue()
    due_date: ExtractedValue = ExtractedValue()
    currency: ExtractedValue = ExtractedValue()
    subtotal: ExtractedValue = ExtractedValue()
    tax_amount: ExtractedValue = ExtractedValue()
    total: ExtractedValue = ExtractedValue()
    line_items: list[LineItem] = Field(default_factory=list)


@dataclass
class GroundedField:
    """Un campo ya verificado contra el OCR y las reglas de negocio."""

    field_name: str
    value: str | None
    confidence: float
    page_number: int | None = None
    bbox: dict | None = None
    source_snippet: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.confidence < settings.field_review_threshold


SYSTEM_PROMPT = """You extract structured data from OCR'd invoices and receipts.

Rules you must follow:
- Return ONLY a JSON object. No prose, no markdown fences.
- For every field, copy `source_text` VERBATIM from the OCR text provided. It
  must appear character-for-character in the input. This is how your output is
  verified.
- If a field is not present in the document, set value to null and confidence
  to 0. Never guess, never infer a plausible value.
- The OCR text may contain errors. Report what the document says, not what it
  should say.
- Amounts: digits and a decimal point only, no currency symbol or thousands
  separator. "1,841.90" becomes "1841.90".
- Dates: ISO format YYYY-MM-DD.
- `confidence` reflects how certain you are that you read the field correctly,
  from 0.0 to 1.0.

Schema:
{
  "vendor_name":    {"value": str|null, "source_text": str|null, "confidence": float},
  "vendor_tax_id":  {...}, "buyer_name": {...}, "buyer_tax_id": {...},
  "invoice_number": {...}, "issue_date": {...}, "due_date": {...},
  "currency":       {...}, "subtotal":   {...}, "tax_amount": {...},
  "total":          {...},
  "line_items": [{"description": str, "quantity": num, "unit_price": num, "amount": num}]
}"""


def build_prompt(pages: list[tuple[int, str]]) -> str:
    blocks = [f"--- PAGE {number} ---\n{text}" for number, text in pages]
    return "Extract the invoice fields from the following OCR output.\n\n" + "\n\n".join(
        blocks
    )


# --------------------------------------------------------------------------
# Normalizacion y anclaje en el OCR
# --------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Minusculas sin acentos ni puntuacion, para comparar de forma tolerante."""
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", stripped.lower())


def find_in_ocr(
    snippet: str, pages_words: dict[int, list[dict]]
) -> tuple[int | None, dict | None, float]:
    """Localiza el fragmento entre las palabras del OCR.

    Devuelve (numero de pagina, bbox que envuelve las palabras, confianza OCR
    media). Si no lo encuentra, la confianza es 0 y el bbox es None: eso es
    exactamente la senal de alucinacion que queremos capturar.
    """
    target = normalize(snippet)
    if not target:
        return None, None, 0.0

    best: tuple[int, list[dict], float] | None = None

    for page_number, words in pages_words.items():
        if not words:
            continue
        normalized = [normalize(w["text"]) for w in words]

        # Ventana deslizante: el fragmento puede abarcar varias palabras.
        for start in range(len(words)):
            joined = ""
            for end in range(start, min(start + 14, len(words))):
                joined += normalized[end]
                if not joined:
                    continue
                if joined == target:
                    span = words[start : end + 1]
                    return page_number, _union_bbox(span), _mean_conf(span)
                if len(joined) > len(target) + 6:
                    break

        # Sin coincidencia exacta, aceptamos parecido alto: el OCR pudo leer
        # "Ferreteria" como "Ferretena" y el modelo copio el original.
        page_text = "".join(normalized)
        if target in page_text:
            ratio = 1.0
        else:
            ratio = difflib.SequenceMatcher(None, target, page_text).quick_ratio()
        if ratio >= 0.92 and (best is None or ratio > best[2]):
            best = (page_number, words, ratio)

    if best is not None:
        page_number, words, _ = best
        approx = _approximate_span(target, words)
        if approx:
            return page_number, _union_bbox(approx), _mean_conf(approx) * 0.85

    return None, None, 0.0


def _approximate_span(target: str, words: list[dict]) -> list[dict]:
    """Devuelve las palabras cuyo texto normalizado aparece dentro del target."""
    matched = [w for w in words if normalize(w["text"]) and normalize(w["text"]) in target]
    return matched[:14]


def _union_bbox(words: list[dict]) -> dict:
    x0 = min(w["x"] for w in words)
    y0 = min(w["y"] for w in words)
    x1 = max(w["x"] + w["w"] for w in words)
    y1 = max(w["y"] + w["h"] for w in words)
    return {
        "x": round(x0, 5),
        "y": round(y0, 5),
        "w": round(x1 - x0, 5),
        "h": round(y1 - y0, 5),
    }


def _mean_conf(words: list[dict]) -> float:
    total = sum(len(w["text"]) for w in words)
    if not total:
        return 0.0
    return sum(w["conf"] * len(w["text"]) for w in words) / total / 100.0


# --------------------------------------------------------------------------
# Validacion por reglas
# --------------------------------------------------------------------------


def parse_money(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", value.replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date_parser.parse(value, dayfirst=False).date()
    except (ValueError, OverflowError):
        return None


def validate(fields: dict[str, GroundedField]) -> None:
    """Aplica reglas de negocio y ajusta la confianza en su lugar.

    La aritmetica es la regla mas util: si subtotal + IVA no da el total,
    al menos uno de los tres esta mal leido, aunque el modelo jure que no.
    """
    subtotal = parse_money(fields["subtotal"].value)
    tax = parse_money(fields["tax_amount"].value)
    total = parse_money(fields["total"].value)

    if subtotal is not None and tax is not None and total is not None:
        if abs(subtotal + tax - total) <= 0.02:
            for name in ("subtotal", "tax_amount", "total"):
                fields[name].notes.append("arithmetic_ok")
        else:
            for name in ("subtotal", "tax_amount", "total"):
                fields[name].confidence *= 0.45
                fields[name].notes.append("arithmetic_mismatch")

    for name in ("vendor_tax_id", "buyer_tax_id"):
        value = fields[name].value
        if value and not NIT_PATTERN.search(value):
            fields[name].confidence *= 0.6
            fields[name].notes.append("tax_id_format_unexpected")

    for name in ("issue_date", "due_date"):
        value = fields[name].value
        if value:
            parsed = parse_date(value)
            if parsed is None:
                fields[name].confidence = 0.0
                fields[name].notes.append("unparseable_date")
            elif not (date(2000, 1, 1) <= parsed <= date(2100, 1, 1)):
                fields[name].confidence *= 0.3
                fields[name].notes.append("date_out_of_range")

    currency = fields["currency"].value
    if currency and currency.upper() not in ISO_CURRENCIES:
        fields["currency"].confidence *= 0.5
        fields["currency"].notes.append("unknown_currency")

    issue = parse_date(fields["issue_date"].value)
    due = parse_date(fields["due_date"].value)
    if issue and due and due < issue:
        fields["due_date"].confidence *= 0.4
        fields["due_date"].notes.append("due_before_issue")


def ground_extraction(
    extraction: InvoiceExtraction, pages_words: dict[int, list[dict]]
) -> dict[str, GroundedField]:
    """Convierte la salida cruda del modelo en campos verificados."""
    fields: dict[str, GroundedField] = {}

    for name in FIELD_NAMES:
        raw: ExtractedValue = getattr(extraction, name)

        if raw.value is None:
            fields[name] = GroundedField(field_name=name, value=None, confidence=0.0)
            continue

        page_number, bbox, ocr_confidence = find_in_ocr(
            raw.source_text or raw.value, pages_words
        )

        if bbox is None:
            # El modelo no pudo citar el origen: tratamos el valor como
            # sospechoso aunque haya reportado alta confianza.
            confidence = min(raw.confidence, 0.35)
            notes = ["not_grounded_in_ocr"]
        else:
            # La senal mas debil manda. Un OCR de 0.4 no puede sostener un
            # campo de 0.95 por mas seguro que suene el modelo.
            confidence = min(raw.confidence, ocr_confidence)
            notes = []

        fields[name] = GroundedField(
            field_name=name,
            value=raw.value,
            confidence=round(confidence, 4),
            page_number=page_number,
            bbox=bbox,
            source_snippet=raw.source_text,
            notes=notes,
        )

    validate(fields)

    for f in fields.values():
        f.confidence = round(max(0.0, min(1.0, f.confidence)), 4)

    return fields


# --------------------------------------------------------------------------
# Extractor heuristico: sirve de modo mock y de linea base en los evals
# --------------------------------------------------------------------------


def heuristic_extraction(pages: list[tuple[int, str]]) -> dict:
    """Extractor por expresiones regulares, sin modelo.

    Cumple doble funcion: permite correr el pipeline completo sin API key,
    y es la linea base contra la que se compara el LLM en el paso 9. Sin una
    linea base, decir "el modelo acierta 91%" no significa nada.
    """
    text = "\n".join(t for _, t in pages)
    result: dict = {
        name: {"value": None, "source_text": None, "confidence": 0.0} for name in FIELD_NAMES
    }
    result["line_items"] = []

    nits = NIT_PATTERN.findall(text)
    if nits:
        result["vendor_tax_id"] = {"value": nits[0], "source_text": nits[0], "confidence": 0.7}
    if len(nits) > 1:
        result["buyer_tax_id"] = {"value": nits[1], "source_text": nits[1], "confidence": 0.7}

    invoice_no = re.search(r"\bFAC-\d{4}-\d{4}\b", text)
    if invoice_no:
        result["invoice_number"] = {
            "value": invoice_no.group(),
            "source_text": invoice_no.group(),
            "confidence": 0.75,
        }

    iso_date = re.search(r"\b20\d{2}-\d{2}-\d{2}\b", text)
    if iso_date:
        result["issue_date"] = {
            "value": iso_date.group(),
            "source_text": iso_date.group(),
            "confidence": 0.7,
        }

    if "USD" in text:
        result["currency"] = {"value": "USD", "source_text": "USD", "confidence": 0.8}

    # El total suele ser la cifra mas grande del documento.
    amounts = [(m.group(), parse_money(m.group())) for m in MONEY_PATTERN.finditer(text)]
    amounts = [(raw, val) for raw, val in amounts if val is not None]
    if amounts:
        raw, val = max(amounts, key=lambda pair: pair[1])
        result["total"] = {"value": f"{val:.2f}", "source_text": raw, "confidence": 0.5}

    first_line = next((line.strip() for line in text.splitlines() if line.strip()), None)
    if first_line:
        result["vendor_name"] = {
            "value": first_line[:200],
            "source_text": first_line[:200],
            "confidence": 0.45,
        }

    return result
