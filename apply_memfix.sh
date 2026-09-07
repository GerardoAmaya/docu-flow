#!/bin/bash
# Memory fix: cambiar de modelo local a Voyage AI para embeddings
# Esto evita cargar intfloat/multilingual-e5-base en memoria (~500MB)

set -e

echo "Applying memory optimization fix..."
echo "- Switching from local embedding model to Voyage AI API"
echo "- EMBEDDING_DIM: 1024 (Voyage 4-lite output dimension)"

# Actualizar .env
python3 - << 'PY'
lines = open(".env").read().splitlines()
out = []
for line in lines:
    if line.startswith("EMBEDDING_MODEL="):
        # Removido - usaremos EMBEDDING_PROVIDER=voyage en su lugar
        continue
    elif line.startswith("EMBEDDING_DIM="):
        out.append("EMBEDDING_DIM=1024")
    else:
        out.append(line)

# Agregar nuevas variables Voyage
out.append("")
out.append("# --- Voyage AI Embeddings (externa, sin costo de tokens) ---")
out.append("EMBEDDING_PROVIDER=voyage")
out.append("VOYAGE_MODEL=voyage-4-lite")
out.append("VOYAGE_API_KEY=<tu-api-key-aqui>")

open(".env", "w").write("\n".join(out) + "\n")
PY

echo "✓ .env updated"
echo ""
echo "Updated values:"
grep -E "EMBEDDING_|VOYAGE_" .env

