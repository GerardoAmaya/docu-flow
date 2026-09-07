#!/usr/bin/env python3
"""Evaluate extraction accuracy against the ground-truth labels.

Produce dos cortes que importan por separado:
  - por campo:      donde falla el sistema
  - por dificultad: donde se rompe

Un promedio global esconde las dos cosas. "88% de exactitud" no le sirve a
nadie; "97% en documentos digitales, 54% en fotos degradadas" le dice a un
equipo exactamente que arreglar y que documentos rechazar en la entrada.

Uso:
    python scripts/evaluate.py
    python scripts/evaluate.py --api http://localhost:8000 --markdown
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GROUND_TRUTH = ROOT / "fixtures" / "ground_truth.json"

MONEY_FIELDS = {"subtotal", "tax_amount", "total"}
DATE_FIELDS = {"issue_date", "due_date"}
COMPARED_FIELDS = [
    "vendor_name",
    "vendor_tax_id",
    "buyer_name",
    "buyer_tax_id",
    "invoice_number",
    "issue_date",
    "currency",
    "subtotal",
    "tax_amount",
    "total",
]


def fetch(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.load(response)
    except urllib.error.URLError as exc:
        sys.exit(f"No pude alcanzar {url}: {exc}")


def normalize_text(value: str) -> str:
    """Minusculas, sin acentos, espacios colapsados.

    Comparamos asi porque "CAFE DE ALTURA" y "Café de Altura" son el mismo
    dato. No toleramos diferencias de contenido, solo de forma.
    """
    decomposed = unicodedata.normalize("NFKD", str(value))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped.lower()).strip().rstrip(".")


def values_match(field: str, expected: str, actual: str | None) -> bool:
    if actual is None or str(actual).strip() == "":
        return False

    if field in MONEY_FIELDS:
        try:
            # Un centavo de tolerancia: 3089.0 y 3089.00 son el mismo monto.
            return abs(float(str(actual).replace(",", "")) - float(expected)) < 0.01
        except ValueError:
            return False

    if field in DATE_FIELDS:
        return str(actual).strip()[:10] == str(expected).strip()[:10]

    return normalize_text(expected) == normalize_text(actual)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--markdown", action="store_true", help="Salida lista para el README")
    args = parser.parse_args()

    if not GROUND_TRUTH.exists():
        sys.exit(f"No encuentro {GROUND_TRUTH}. Corre scripts/generate_fixtures.py primero.")

    truth = {t["file"]: t for t in json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))}
    documents = fetch(f"{args.api}/documents?limit=200")["items"]

    by_field: dict[str, list[int]] = defaultdict(list)
    by_difficulty: dict[str, list[int]] = defaultdict(list)
    review_flagged: dict[str, list[int]] = defaultdict(list)
    # Abstenerse no es equivocarse. Un campo nulo sobre una imagen ilegible es
    # el comportamiento correcto: el sistema reconoce que no puede leerlo y lo
    # deriva a un humano. Mezclarlo con las respuestas erroneas hace que un
    # modelo prudente puntue igual que uno que inventa.
    abstained: dict[str, list[int]] = defaultdict(list)
    abstained_by_difficulty: dict[str, list[int]] = defaultdict(list)
    misses: list[tuple[str, str, str, str, float]] = []
    evaluated = 0

    for doc in documents:
        expected = truth.get(doc["filename"])
        if expected is None:
            continue
        evaluated += 1
        difficulty = expected["difficulty"]

        fields = {
            f["field_name"]: f
            for f in fetch(f"{args.api}/documents/{doc['id']}/fields")["items"]
        }

        for name in COMPARED_FIELDS:
            if name not in expected:
                continue
            field = fields.get(name, {})
            # El valor corregido gana: mide el sistema completo, humano incluido.
            actual = field.get("corrected_value") or field.get("value_text")
            hit = values_match(name, expected[name], actual)
            is_abstention = actual is None or str(actual).strip() == ""

            by_field[name].append(hit)
            by_difficulty[difficulty].append(hit)
            review_flagged[name].append(bool(field.get("needs_review")))
            abstained[name].append(is_abstention)
            abstained_by_difficulty[difficulty].append(is_abstention)

            if not hit and not is_abstention:
                misses.append(
                    (doc["filename"], name, expected[name], str(actual),
                     float(field.get("confidence") or 0.0))
                )

    if not evaluated:
        sys.exit("Ningun documento coincide con el ground truth. Subiste los fixtures?")

    def pct(values: list[int]) -> float:
        return 100 * sum(values) / len(values) if values else 0.0

    overall = [hit for values in by_field.values() for hit in values]
    all_abstained = [a for values in abstained.values() for a in values]

    def answered_precision(hits: list[int], skips: list[int]) -> float:
        """Exactitud contando solo los campos que el sistema si respondio."""
        answered = [h for h, skip in zip(hits, skips) if not skip]
        return 100 * sum(answered) / len(answered) if answered else 0.0

    if args.markdown:
        print(f"Evaluado sobre {evaluated} documentos, {len(overall)} campos.\n")
        print("| Campo | Exactitud | n |")
        print("|---|---|---|")
        for name in COMPARED_FIELDS:
            if by_field[name]:
                print(f"| `{name}` | {pct(by_field[name]):.0f}% | {len(by_field[name])} |")
        print(f"| **Global** | **{pct(overall):.1f}%** | {len(overall)} |")
        print("\n| Dificultad | Cobertura | Exactitud si responde | Abstencion | n |")
        print("|---|---|---|---|---|")
        for level in ("clean", "scanned", "photo", "hard"):
            if by_difficulty[level]:
                print(
                    f"| {level} | {pct(by_difficulty[level]):.0f}% "
                    f"| {answered_precision(by_difficulty[level], abstained_by_difficulty[level]):.0f}% "
                    f"| {pct(abstained_by_difficulty[level]):.0f}% "
                    f"| {len(by_difficulty[level])} |"
                )
    else:
        print(f"\nEvaluado sobre {evaluated} documentos, {len(overall)} campos\n")
        print(f"{'campo':16} {'cobertura':>10} {'si resp.':>10} {'abstuvo':>9} {'marcado':>9}   n")
        print("-" * 66)
        for name in COMPARED_FIELDS:
            if by_field[name]:
                print(f"{name:16} {pct(by_field[name]):9.0f}% "
                      f"{answered_precision(by_field[name], abstained[name]):9.0f}% "
                      f"{pct(abstained[name]):8.0f}% {pct(review_flagged[name]):8.0f}% "
                      f"{len(by_field[name]):4}")
        print("-" * 66)
        print(f"{'GLOBAL':16} {pct(overall):9.1f}% "
              f"{answered_precision(overall, all_abstained):9.1f}% "
              f"{pct(all_abstained):8.0f}% {'':8} {len(overall):4}\n")

        print(f"{'dificultad':16} {'cobertura':>10} {'si resp.':>10} {'abstuvo':>9}   n")
        print("-" * 58)
        for level in ("clean", "scanned", "photo", "hard"):
            if by_difficulty[level]:
                print(f"{level:16} {pct(by_difficulty[level]):9.0f}% "
                      f"{answered_precision(by_difficulty[level], abstained_by_difficulty[level]):9.0f}% "
                      f"{pct(abstained_by_difficulty[level]):8.0f}% "
                      f"{len(by_difficulty[level]):4}")

        if misses:
            print(f"\n{len(misses)} respuestas incorrectas "
                  "(no incluye abstenciones):\n")
            print(f"{'archivo':16} {'campo':15} {'esperado':22} {'obtenido':22} {'conf':>6}")
            print("-" * 86)
            for filename, name, expected_value, actual, confidence in misses[:30]:
                print(f"{filename:16} {name:15} {str(expected_value)[:21]:22} "
                      f"{actual[:21]:22} {confidence:6.3f}")

            # Un fallo con confianza alta es peor que uno con confianza baja:
            # el sistema no solo se equivoco, ademas no lo detecto.
            silent = [m for m in misses if m[4] >= 0.85]
            if silent:
                print(f"\nATENCION: {len(silent)} fallos con confianza >= 0.85 "
                      "(errores que el sistema no detecto)")


if __name__ == "__main__":
    main()
