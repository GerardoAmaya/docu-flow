#!/usr/bin/env python3
"""Generate synthetic invoice fixtures with ground-truth labels.

Produce tres niveles de dificultad a proposito:
  - clean:    PDF vectorial, como una factura emitida por un sistema
  - scanned:  imagen rasterizada con ruido y leve rotacion
  - photo:    foto de celular simulada: sombra, desenfoque, perspectiva

Un pipeline de OCR que solo se prueba con PDFs limpios da una falsa sensacion
de precision. Los tres niveles son los que vas a encontrar en la realidad.

Uso:  python scripts/generate_fixtures.py
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

SEED = 20260907
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "invoices"

# El Salvador usa USD desde 2001 y el IVA es 13%.
IVA_RATE = 0.13
CURRENCY = "USD"

VENDORS = [
    ("Ferreteria El Constructor, S.A. de C.V.", "0614-180592-102-3"),
    ("Distribuidora Cuscatlan, S.A. de C.V.", "0614-230785-001-8"),
    ("Servicios Informaticos Pipil, S.A. de C.V.", "0614-051199-114-2"),
    ("Panaderia La Espiga Dorada", "0614-120380-103-5"),
    ("Transportes del Pacifico, S.A. de C.V.", "0614-081193-107-0"),
    ("Suministros Medicos Izalco, S.A. de C.V.", "0614-170294-108-9"),
    ("Cafe de Altura Ahuachapan, S.A. de C.V.", "0614-021087-101-4"),
    ("Papeleria y Libreria Morazan", "0614-140675-106-1"),
    ("Refrigeracion Industrial Lempa, S.A. de C.V.", "0614-260391-112-7"),
]

BUYER = ("Consultores Asociados GA, S.A. de C.V.", "0614-110698-105-6")

CATALOG = [
    ("Cemento gris tipo I, bolsa 42.5 kg", 9.75),
    ("Varilla corrugada 3/8 x 6m", 7.40),
    ("Servicio de mantenimiento preventivo", 145.00),
    ("Licencia de software, mensual", 38.50),
    ("Resma de papel bond carta", 4.95),
    ("Toner compatible HP 05A", 32.00),
    ("Cafe molido premium, libra", 6.80),
    ("Flete terrestre San Salvador - Santa Ana", 85.00),
    ("Guantes de nitrilo, caja 100 unidades", 12.25),
    ("Mascarilla quirurgica, caja 50 unidades", 8.90),
    ("Pan frances, ciento", 11.00),
    ("Instalacion de aire acondicionado 12000 BTU", 275.00),
]


@dataclass
class GroundTruth:
    """Etiquetas de referencia. Es contra esto que se miden los evals."""

    file: str
    difficulty: str
    vendor_name: str
    vendor_tax_id: str
    buyer_name: str
    buyer_tax_id: str
    invoice_number: str
    issue_date: str
    currency: str
    subtotal: str
    tax_amount: str
    total: str
    line_item_count: int


def money(value: float) -> str:
    return f"{value:.2f}"


def build_invoice_data(rng: random.Random, index: int) -> dict:
    vendor_name, vendor_tax_id = VENDORS[index % len(VENDORS)]
    items = rng.sample(CATALOG, rng.randint(2, 5))

    lines = []
    subtotal = 0.0
    for description, unit_price in items:
        quantity = rng.randint(1, 12)
        amount = round(quantity * unit_price, 2)
        subtotal += amount
        lines.append(
            {
                "description": description,
                "quantity": quantity,
                "unit_price": unit_price,
                "amount": amount,
            }
        )

    subtotal = round(subtotal, 2)
    tax_amount = round(subtotal * IVA_RATE, 2)
    total = round(subtotal + tax_amount, 2)

    issue = date(2026, 1, 1) + timedelta(days=rng.randint(0, 240))

    return {
        "vendor_name": vendor_name,
        "vendor_tax_id": vendor_tax_id,
        "buyer_name": BUYER[0],
        "buyer_tax_id": BUYER[1],
        "invoice_number": f"FAC-{2026}-{rng.randint(1000, 9999)}",
        "issue_date": issue.isoformat(),
        "due_date": (issue + timedelta(days=30)).isoformat(),
        "lines": lines,
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "total": total,
    }


# --------------------------------------------------------------------------
# Nivel 1: PDF vectorial limpio
# --------------------------------------------------------------------------

def render_pdf(data: dict, path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=LETTER)
    width, height = LETTER
    left = 0.75 * inch
    y = height - 0.9 * inch

    c.setFont("Helvetica-Bold", 15)
    c.drawString(left, y, data["vendor_name"][:52])
    y -= 16
    c.setFont("Helvetica", 9.5)
    c.drawString(left, y, f"NIT: {data['vendor_tax_id']}")
    y -= 12
    c.drawString(left, y, "San Salvador, El Salvador")

    c.setFont("Helvetica-Bold", 17)
    c.drawRightString(width - left, height - 0.9 * inch, "FACTURA")
    c.setFont("Helvetica", 10)
    c.drawRightString(width - left, height - 0.9 * inch - 18, f"No. {data['invoice_number']}")
    c.drawRightString(width - left, height - 0.9 * inch - 32, f"Fecha de emision: {data['issue_date']}")
    c.drawRightString(width - left, height - 0.9 * inch - 46, f"Vencimiento: {data['due_date']}")

    y -= 42
    c.setFont("Helvetica-Bold", 10)
    c.drawString(left, y, "Cliente")
    y -= 14
    c.setFont("Helvetica", 9.5)
    c.drawString(left, y, data["buyer_name"])
    y -= 12
    c.drawString(left, y, f"NIT: {data['buyer_tax_id']}")

    y -= 28
    c.setFillColorRGB(0.92, 0.92, 0.92)
    c.rect(left, y - 4, width - 2 * left, 18, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(left + 4, y + 2, "Descripcion")
    c.drawRightString(left + 350, y + 2, "Cant.")
    c.drawRightString(left + 430, y + 2, "P. Unitario")
    c.drawRightString(width - left - 4, y + 2, "Importe")

    y -= 20
    c.setFont("Helvetica", 9)
    for line in data["lines"]:
        c.drawString(left + 4, y, line["description"][:52])
        c.drawRightString(left + 350, y, str(line["quantity"]))
        c.drawRightString(left + 430, y, money(line["unit_price"]))
        c.drawRightString(width - left - 4, y, money(line["amount"]))
        y -= 15

    y -= 10
    c.line(left + 300, y, width - left, y)
    y -= 16
    for label, value in (
        ("Subtotal", data["subtotal"]),
        (f"IVA {int(IVA_RATE * 100)}%", data["tax_amount"]),
    ):
        c.drawRightString(left + 430, y, f"{label}:")
        c.drawRightString(width - left - 4, y, f"{CURRENCY} {money(value)}")
        y -= 15

    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(left + 430, y, "TOTAL:")
    c.drawRightString(width - left - 4, y, f"{CURRENCY} {money(data['total'])}")

    c.setFont("Helvetica", 7.5)
    c.drawString(left, 0.7 * inch, "Documento generado para pruebas. No tiene validez fiscal.")
    c.showPage()
    c.save()


# --------------------------------------------------------------------------
# Niveles 2 y 3: imagen rasterizada y degradada
# --------------------------------------------------------------------------

def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def render_image(data: dict) -> Image.Image:
    """Dibuja la factura como imagen a 150 DPI sobre tamano carta."""
    W, H = 1275, 1650
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    m = 90
    y = 80

    d.text((m, y), data["vendor_name"][:48], font=_font(26, bold=True), fill="black")
    y += 36
    d.text((m, y), f"NIT: {data['vendor_tax_id']}", font=_font(18), fill="black")
    y += 26
    d.text((m, y), "San Salvador, El Salvador", font=_font(18), fill="black")

    d.text((W - m - 200, 80), "FACTURA", font=_font(30, bold=True), fill="black")
    d.text((W - m - 320, 124), f"No. {data['invoice_number']}", font=_font(18), fill="black")
    d.text((W - m - 320, 150), f"Fecha: {data['issue_date']}", font=_font(18), fill="black")
    d.text((W - m - 320, 176), f"Vence: {data['due_date']}", font=_font(18), fill="black")

    y += 60
    d.text((m, y), "Cliente:", font=_font(19, bold=True), fill="black")
    y += 28
    d.text((m, y), data["buyer_name"], font=_font(18), fill="black")
    y += 26
    d.text((m, y), f"NIT: {data['buyer_tax_id']}", font=_font(18), fill="black")

    y += 55
    d.rectangle([m, y, W - m, y + 32], fill=(232, 232, 232))
    d.text((m + 8, y + 7), "Descripcion", font=_font(17, bold=True), fill="black")
    d.text((m + 640, y + 7), "Cant.", font=_font(17, bold=True), fill="black")
    d.text((m + 740, y + 7), "P. Unit.", font=_font(17, bold=True), fill="black")
    d.text((m + 890, y + 7), "Importe", font=_font(17, bold=True), fill="black")
    y += 44

    for line in data["lines"]:
        d.text((m + 8, y), line["description"][:46], font=_font(17), fill="black")
        d.text((m + 660, y), str(line["quantity"]), font=_font(17), fill="black")
        d.text((m + 745, y), money(line["unit_price"]), font=_font(17), fill="black")
        d.text((m + 895, y), money(line["amount"]), font=_font(17), fill="black")
        y += 30

    y += 20
    d.line([m + 560, y, W - m, y], fill="black", width=2)
    y += 18
    for label, value in (
        ("Subtotal:", data["subtotal"]),
        (f"IVA {int(IVA_RATE * 100)}%:", data["tax_amount"]),
    ):
        d.text((m + 640, y), label, font=_font(18), fill="black")
        d.text((m + 880, y), f"{CURRENCY} {money(value)}", font=_font(18), fill="black")
        y += 30

    d.text((m + 640, y), "TOTAL:", font=_font(22, bold=True), fill="black")
    d.text((m + 860, y), f"{CURRENCY} {money(data['total'])}", font=_font(22, bold=True), fill="black")

    d.text((m, H - 90), "Documento generado para pruebas. No tiene validez fiscal.",
           font=_font(14), fill=(90, 90, 90))
    return img


def degrade_scanned(img: Image.Image, rng: random.Random) -> Image.Image:
    """Simula un escaner: leve rotacion, grano y contraste desparejo."""
    img = img.rotate(rng.uniform(-1.2, 1.2), resample=Image.BICUBIC,
                     fillcolor=(255, 255, 255), expand=False)
    array = np.array(img).astype(np.int16)
    noise = np.random.default_rng(rng.randint(0, 10**6)).normal(0, 9, array.shape)
    array = np.clip(array + noise, 0, 255).astype(np.uint8)
    img = Image.fromarray(array)
    return img.filter(ImageFilter.GaussianBlur(radius=0.45))


def degrade_photo(img: Image.Image, rng: random.Random) -> Image.Image:
    """Simula una foto de celular: sombra diagonal, rotacion mayor, desenfoque."""
    img = img.rotate(rng.uniform(-3.5, 3.5), resample=Image.BICUBIC,
                     fillcolor=(255, 255, 255), expand=False)
    array = np.array(img).astype(np.float32)
    h, w = array.shape[:2]

    # Gradiente de iluminacion: un lado queda notablemente mas oscuro.
    gx = np.linspace(rng.uniform(0.55, 0.7), 1.05, w)
    gy = np.linspace(1.0, rng.uniform(0.72, 0.88), h)
    shading = np.outer(gy, gx)[:, :, None]
    array = array * shading

    rng_np = np.random.default_rng(rng.randint(0, 10**6))
    array = np.clip(array + rng_np.normal(0, 13, array.shape), 0, 255).astype(np.uint8)

    img = Image.fromarray(array)
    return img.filter(ImageFilter.GaussianBlur(radius=0.9))


# --------------------------------------------------------------------------
# DTE: factura electronica en JSON
# --------------------------------------------------------------------------

def degrade_hard(img: Image.Image, rng: random.Random) -> Image.Image:
    """Caso limite: foto movida, muy inclinada y con poco contraste.

    Existe a proposito para que la cola de revision humana tenga trabajo.
    Un set de prueba donde todo sale bien no ejercita el camino que importa.
    """
    img = img.rotate(rng.uniform(-7.5, 7.5), resample=Image.BICUBIC,
                     fillcolor=(255, 255, 255), expand=False)
    array = np.array(img).astype(np.float32)
    h, w = array.shape[:2]

    gx = np.linspace(rng.uniform(0.32, 0.42), 0.95, w)
    gy = np.linspace(0.98, rng.uniform(0.45, 0.6), h)
    array = array * np.outer(gy, gx)[:, :, None]

    # Aplasta el rango dinamico: gris sobre gris, como una fotocopia gastada.
    array = 62 + array * 0.62

    rng_np = np.random.default_rng(rng.randint(0, 10**6))
    array = np.clip(array + rng_np.normal(0, 26, array.shape), 0, 255).astype(np.uint8)

    img = Image.fromarray(array)
    return img.filter(ImageFilter.GaussianBlur(radius=1.9))


def render_dte_json(data: dict, path: Path) -> None:
    """Estructura simplificada inspirada en el DTE salvadoreno.

    No pretende ser el esquema oficial; sirve para ejercitar la rama del
    pipeline que recibe datos ya estructurados y no necesita OCR.
    """
    payload = {
        "identificacion": {
            "version": 1,
            "tipoDte": "01",
            "numeroControl": data["invoice_number"],
            "fecEmi": data["issue_date"],
            "tipoMoneda": CURRENCY,
        },
        "emisor": {"nit": data["vendor_tax_id"], "nombre": data["vendor_name"]},
        "receptor": {"nit": data["buyer_tax_id"], "nombre": data["buyer_name"]},
        "cuerpoDocumento": [
            {
                "numItem": i + 1,
                "descripcion": line["description"],
                "cantidad": line["quantity"],
                "precioUni": line["unit_price"],
                "ventaGravada": line["amount"],
            }
            for i, line in enumerate(data["lines"])
        ],
        "resumen": {
            "subTotal": data["subtotal"],
            "tributos": [{"codigo": "20", "descripcion": "IVA 13%", "valor": data["tax_amount"]}],
            "montoTotalOperacion": data["total"],
        },
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    rng = random.Random(SEED)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    plan = [
        ("clean", 4),
        ("scanned", 3),
        ("photo", 3),
        ("hard", 2),
        ("dte", 1),
    ]

    truths: list[GroundTruth] = []
    index = 0

    for difficulty, count in plan:
        for n in range(count):
            data = build_invoice_data(rng, index)
            index += 1
            stem = f"{difficulty}_{n + 1:02d}"

            if difficulty == "clean":
                filename = f"{stem}.pdf"
                render_pdf(data, OUTPUT_DIR / filename)
            elif difficulty == "dte":
                filename = f"{stem}.json"
                render_dte_json(data, OUTPUT_DIR / filename)
            else:
                image = render_image(data)
                degrader = {
                    "scanned": degrade_scanned,
                    "photo": degrade_photo,
                    "hard": degrade_hard,
                }[difficulty]
                image = degrader(image, rng)
                filename = f"{stem}.jpg"
                image.save(OUTPUT_DIR / filename, "JPEG", quality=78, optimize=True)

            truths.append(
                GroundTruth(
                    file=filename,
                    difficulty=difficulty,
                    vendor_name=data["vendor_name"],
                    vendor_tax_id=data["vendor_tax_id"],
                    buyer_name=data["buyer_name"],
                    buyer_tax_id=data["buyer_tax_id"],
                    invoice_number=data["invoice_number"],
                    issue_date=data["issue_date"],
                    currency=CURRENCY,
                    subtotal=money(data["subtotal"]),
                    tax_amount=money(data["tax_amount"]),
                    total=money(data["total"]),
                    line_item_count=len(data["lines"]),
                )
            )

    truth_path = OUTPUT_DIR.parent / "ground_truth.json"
    truth_path.write_text(
        json.dumps([asdict(t) for t in truths], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"{len(truths)} documentos generados en {OUTPUT_DIR}")
    print(f"Etiquetas de referencia en {truth_path}")
    for t in truths:
        print(f"  {t.difficulty:8} {t.file:16} total={t.total:>9} {t.vendor_name[:38]}")


if __name__ == "__main__":
    main()
