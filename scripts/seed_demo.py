#!/usr/bin/env python3
"""Seed a DocuFlow instance with the fixture invoices.

Un reclutador que abre la demo y encuentra una lista vacia se va. Este script
deja las 12 facturas procesadas, con la cola de revision poblada, para que la
primera pantalla ya muestre algo.

Reutiliza el endpoint publico de subida en vez de restaurar un dump: asi el
seed ejercita el pipeline real y falla ruidosamente si el despliegue esta mal.
La deduplicacion por hash lo hace idempotente, se puede correr las veces que
haga falta.

Uso:
    python scripts/seed_demo.py
    python scripts/seed_demo.py --api https://tu-api.up.railway.app
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "invoices"
TERMINAL_STATES = {"completed", "needs_review", "failed"}


def post_file(api: str, path: Path) -> dict:
    """Sube un archivo con multipart/form-data usando solo la stdlib."""
    boundary = f"----docuflow{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])

    request = urllib.request.Request(
        f"{api}/documents",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def wait_until_processed(api: str, document_ids: list[str], timeout_s: int) -> dict:
    """Espera a que todos los documentos lleguen a un estado final."""
    deadline = time.time() + timeout_s
    last: dict[str, str] = {}

    while time.time() < deadline:
        states = {}
        for document_id in document_ids:
            try:
                states[document_id] = get_json(f"{api}/documents/{document_id}")["status"]
            except urllib.error.URLError:
                states[document_id] = "unreachable"

        done = sum(1 for s in states.values() if s in TERMINAL_STATES)
        if states != last:
            print(f"  {done}/{len(document_ids)} listos", flush=True)
            last = states
        if done == len(document_ids):
            return states
        time.sleep(5)

    print("  Se agoto el tiempo de espera.", file=sys.stderr)
    return last


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--skip-wait", action="store_true", help="Sube sin esperar el procesamiento"
    )
    args = parser.parse_args()
    api = args.api.rstrip("/")

    if not FIXTURES.exists():
        sys.exit(f"No encuentro {FIXTURES}. Corre scripts/generate_fixtures.py primero.")

    try:
        health = get_json(f"{api}/health")
    except urllib.error.URLError as exc:
        sys.exit(f"No pude alcanzar {api}/health: {exc}")

    print(f"API: {api}")
    print(f"  pgvector={health.get('pgvector')}  mock_llm={health.get('mock_llm')}")
    if health.get("mock_llm"):
        print("  AVISO: la instancia esta en modo mock; la extraccion sera heuristica.")
    print()

    files = sorted(p for p in FIXTURES.iterdir() if p.suffix.lower() != ".json")
    document_ids: list[str] = []
    duplicates = 0

    print(f"Subiendo {len(files)} documentos...")
    for path in files:
        try:
            result = post_file(api, path)
        except urllib.error.HTTPError as exc:
            print(f"  {path.name:18} FALLO {exc.code}: {exc.read()[:120]!r}", file=sys.stderr)
            continue
        document_ids.append(result["document"]["id"])
        if result.get("duplicate"):
            duplicates += 1
        print(f"  {path.name:18} {'ya existia' if result.get('duplicate') else 'subido'}")

    if duplicates:
        print(f"\n{duplicates} ya estaban cargados; no se reprocesaron ni gastaron tokens.")

    if args.skip_wait or not document_ids:
        return

    print("\nEsperando el procesamiento...")
    states = wait_until_processed(api, document_ids, args.timeout)

    counts: dict[str, int] = {}
    for state in states.values():
        counts[state] = counts.get(state, 0) + 1

    print("\nResultado:")
    for state, count in sorted(counts.items()):
        print(f"  {state:16} {count}")

    try:
        queue = get_json(f"{api}/review/queue")["items"]
        print(f"\nCola de revision: {len(queue)} documentos")
        for item in queue:
            print(f"  {item['filename']:18} {item['pending_fields']} campos pendientes")
    except urllib.error.URLError:
        pass

    if counts.get("failed"):
        sys.exit(f"{counts['failed']} documentos fallaron. Revisa los logs del worker.")


if __name__ == "__main__":
    main()
