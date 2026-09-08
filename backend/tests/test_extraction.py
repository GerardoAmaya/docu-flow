"""Unit tests for the extraction logic. No necesitan base de datos.

Cubren las tres senales que determinan la confianza de un campo, que es el
corazon del sistema: si esto se rompe, el sistema empieza a dar por buenos
valores que invento el modelo.
"""

from __future__ import annotations

import pytest

from app.services.extraction import (
    InvoiceExtraction,
    find_in_ocr,
    ground_extraction,
    heuristic_extraction,
    normalize,
    parse_date,
    parse_money,
)

# Palabras del OCR con bbox normalizado, como las produce Tesseract.
OCR_WORDS = [
    {"text": "Ferreteria", "conf": 96.0, "x": 0.08, "y": 0.05, "w": 0.10, "h": 0.02},
    {"text": "El", "conf": 95.0, "x": 0.19, "y": 0.05, "w": 0.02, "h": 0.02},
    {"text": "Constructor", "conf": 94.0, "x": 0.22, "y": 0.05, "w": 0.12, "h": 0.02},
    {"text": "NIT:", "conf": 93.0, "x": 0.08, "y": 0.09, "w": 0.04, "h": 0.02},
    {"text": "0614-180592-102-3", "conf": 92.0, "x": 0.13, "y": 0.09, "w": 0.16, "h": 0.02},
    {"text": "FAC-2026-5216", "conf": 91.0, "x": 0.70, "y": 0.09, "w": 0.15, "h": 0.02},
    {"text": "Subtotal:", "conf": 95.0, "x": 0.60, "y": 0.80, "w": 0.09, "h": 0.02},
    {"text": "1630.00", "conf": 96.0, "x": 0.80, "y": 0.80, "w": 0.08, "h": 0.02},
    {"text": "211.90", "conf": 96.0, "x": 0.80, "y": 0.84, "w": 0.07, "h": 0.02},
    {"text": "1841.90", "conf": 96.0, "x": 0.80, "y": 0.88, "w": 0.08, "h": 0.02},
]

PAGES = {1: OCR_WORDS}


def build(**fields) -> InvoiceExtraction:
    """Arma una extraccion con los campos dados y el resto vacio."""
    empty = {"value": None, "source_text": None, "confidence": 0.0}
    names = [
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
    raw = {name: dict(empty) for name in names}
    raw["line_items"] = []
    for name, (value, confidence) in fields.items():
        raw[name] = {"value": value, "source_text": value, "confidence": confidence}
    return InvoiceExtraction.model_validate(raw)


class TestGrounding:
    """El anclaje es lo que distingue un valor leido de uno inventado."""

    @pytest.mark.parametrize(
        "snippet",
        ["0614-180592-102-3", "FAC-2026-5216", "1841.90", "Ferreteria El Constructor"],
    )
    def test_real_values_are_located(self, snippet):
        page, bbox, confidence = find_in_ocr(snippet, PAGES)
        assert bbox is not None, f"No se anclo un valor real: {snippet}"
        assert page == 1
        assert confidence > 0.85

    @pytest.mark.parametrize(
        "snippet", ["FAC-9999-0000", "0614-999999-999-9", "Panaderia La Espiga"]
    )
    def test_invented_values_are_rejected(self, snippet):
        _, bbox, confidence = find_in_ocr(snippet, PAGES)
        assert bbox is None, f"Se anclo un valor inventado: {snippet}"
        assert confidence == 0.0

    def test_bbox_wraps_every_word_of_the_span(self):
        _, bbox, _ = find_in_ocr("Ferreteria El Constructor", PAGES)
        assert bbox["x"] == pytest.approx(0.08, abs=0.001)
        # Debe llegar hasta el final de "Constructor": 0.22 + 0.12
        assert bbox["x"] + bbox["w"] == pytest.approx(0.34, abs=0.001)

    def test_ungrounded_field_is_capped_even_if_the_model_is_confident(self):
        fields = ground_extraction(build(invoice_number=("FAC-9999-0000", 0.99)), PAGES)
        field = fields["invoice_number"]
        assert field.confidence <= 0.35
        assert "not_grounded_in_ocr" in field.notes
        assert field.needs_review

    def test_confidence_never_exceeds_the_ocr_confidence(self):
        """La senal mas debil manda: un OCR flojo no sostiene un campo seguro."""
        weak = [dict(w, conf=40.0) for w in OCR_WORDS]
        fields = ground_extraction(build(total=("1841.90", 0.99)), {1: weak})
        assert fields["total"].confidence <= 0.45


class TestValidation:
    def test_consistent_arithmetic_is_accepted(self):
        fields = ground_extraction(
            build(
                subtotal=("1630.00", 0.95),
                tax_amount=("211.90", 0.95),
                total=("1841.90", 0.95),
            ),
            PAGES,
        )
        assert "arithmetic_ok" in fields["total"].notes
        assert not fields["total"].needs_review

    def test_inconsistent_arithmetic_penalises_all_three_amounts(self):
        fields = ground_extraction(
            build(
                subtotal=("1630.00", 0.95),
                tax_amount=("211.90", 0.95),
                total=("9999.99", 0.95),
            ),
            PAGES,
        )
        for name in ("subtotal", "tax_amount", "total"):
            assert "arithmetic_mismatch" in fields[name].notes
            assert fields[name].needs_review

    def test_malformed_tax_id_loses_confidence(self):
        fields = ground_extraction(build(vendor_tax_id=("0614.180592.102.3", 0.95)), PAGES)
        assert "tax_id_format_unexpected" in fields["vendor_tax_id"].notes

    def test_unparseable_date_is_zeroed(self):
        fields = ground_extraction(build(issue_date=("no es una fecha", 0.9)), PAGES)
        assert fields["issue_date"].confidence == 0.0
        assert "unparseable_date" in fields["issue_date"].notes

    def test_due_date_before_issue_date_is_flagged(self):
        fields = ground_extraction(
            build(issue_date=("2026-05-10", 0.9), due_date=("2026-01-01", 0.9)), PAGES
        )
        assert "due_before_issue" in fields["due_date"].notes

    def test_null_field_stays_at_zero_and_is_not_queued(self):
        """Abstenerse no es equivocarse: un nulo no va a la cola de revision."""
        fields = ground_extraction(build(), PAGES)
        assert fields["total"].value is None
        assert fields["total"].confidence == 0.0


class TestParsing:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1841.90", 1841.90),
            ("1,841.90", 1841.90),
            ("USD 1841.90", 1841.90),
            ("$1841.90", 1841.90),
            (None, None),
            ("", None),
            ("abc", None),
        ],
    )
    def test_money(self, raw, expected):
        assert parse_money(raw) == expected

    def test_iso_date(self):
        parsed = parse_date("2026-01-23")
        assert parsed is not None and parsed.isoformat() == "2026-01-23"

    def test_normalize_ignores_accents_case_and_punctuation(self):
        assert normalize("Café de Altura, S.A.") == normalize("CAFE DE ALTURA SA")


class TestHeuristicBaseline:
    """El extractor por regex sirve de modo mock y de linea base en los evals."""

    TEXT = (
        "Ferreteria El Constructor, S.A. de C.V.\n"
        "NIT: 0614-180592-102-3\n"
        "No. FAC-2026-5216\nFecha: 2026-01-23\n"
        "Subtotal: USD 1630.00\nIVA 13%: USD 211.90\nTOTAL: USD 1841.90"
    )

    def test_finds_fields_with_a_fixed_shape(self):
        result = heuristic_extraction([(1, self.TEXT)])
        assert result["vendor_tax_id"]["value"] == "0614-180592-102-3"
        assert result["invoice_number"]["value"] == "FAC-2026-5216"
        assert result["issue_date"]["value"] == "2026-01-23"

    def test_picks_the_largest_amount_as_total(self):
        """Documenta la limitacion que justifica usar un modelo.

        La heuristica no entiende que numero es el total: agarra el mayor. Si
        una linea de detalle supera al total, se equivoca.
        """
        result = heuristic_extraction([(1, self.TEXT)])
        assert result["total"]["value"] == "1841.90"

        con_linea_grande = self.TEXT + "\nServicio especial 1 5000.00 5000.00"
        result = heuristic_extraction([(1, con_linea_grande)])
        assert result["total"]["value"] == "5000.00"  # incorrecto, y esperado
