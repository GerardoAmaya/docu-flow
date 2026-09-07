#!/usr/bin/env bash
# Arranque de produccion: migra la base y levanta API y worker en el mismo
# contenedor.
#
# Por que juntos: ambos procesos necesitan el mismo /data (el worker escribe
# las imagenes de pagina, la API las sirve) y los volumenes de los PaaS se
# montan en un solo servicio. En local siguen separados via docker-compose.
#
# Limitacion asumida: no se pueden escalar de forma independiente. Con volumen
# de trabajo real habria que mover los archivos a almacenamiento de objetos y
# separarlos de nuevo.
set -euo pipefail

echo "Ejecutando migraciones..."
alembic upgrade head

# --pool=solo evita el proceso hijo de prefork. Con prefork el modelo de
# embeddings queda cargado dos veces y el contenedor se queda sin memoria.
celery -A app.workers.celery_app worker \
  --loglevel="${CELERY_LOG_LEVEL:-info}" \
  --pool="${CELERY_POOL:-solo}" &
worker_pid=$!

uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" &
api_pid=$!

# Sin esto el PaaS mata el contenedor a la fuerza y las tareas en vuelo se
# pierden en vez de terminar.
shutdown() {
  echo "Apagando..."
  kill -TERM "$worker_pid" "$api_pid" 2>/dev/null || true
  wait "$worker_pid" "$api_pid" 2>/dev/null || true
  exit 0
}
trap shutdown TERM INT

# Si cualquiera de los dos muere, el contenedor entero cae y el PaaS lo
# reinicia. Es preferible a quedar sirviendo la API con el worker caido.
wait -n
echo "Un proceso termino inesperadamente; cerrando el contenedor."
shutdown
