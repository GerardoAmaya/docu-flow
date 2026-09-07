# Despliegue

Cuatro piezas: Postgres con pgvector, Redis, el backend, y el frontend.
La combinación de abajo es la que menos cuentas y menos configuración pide.

| Pieza | Proveedor | Por qué |
|---|---|---|
| Postgres + pgvector | Neon | Capa gratuita con pgvector disponible |
| Redis | Upstash | Capa gratuita, protocolo Redis estándar |
| Backend (API + worker) | Railway | Construye el Dockerfile y permite montar un volumen |
| Frontend | Vercel | Next.js sin configuración |

---

## 1. Postgres

Creá un proyecto en Neon y habilitá la extensión desde su consola SQL:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

Copiá la cadena de conexión y adaptá el prefijo, porque Neon la entrega en
formato `postgresql://` y este proyecto usa el driver psycopg 3:

```
postgresql+psycopg://usuario:clave@host/basedatos?sslmode=require
```

Las migraciones corren solas al arrancar el contenedor; no hace falta
ejecutarlas a mano.

## 2. Redis

Creá una base en Upstash y copiá la URL `rediss://` (con dos eses: es TLS).

## 3. Backend

En Railway, creá un servicio desde el repositorio de GitHub y apuntá el
directorio raíz del build a `backend/`.

**Montá un volumen en `/data`.** Sin él, las imágenes de página se pierden en
cada despliegue y la pantalla de revisión queda sin fondo.

Variables de entorno:

```
DATABASE_URL=postgresql+psycopg://...?sslmode=require
REDIS_URL=rediss://...
ANTHROPIC_API_KEY=sk-ant-...
MOCK_LLM=false
CORS_ORIGINS=https://tu-frontend.vercel.app
STORAGE_DIR=/data/uploads
PAGE_IMAGE_DIR=/data/pages
CELERY_CONCURRENCY=1
CHUNK_SIZE_CHARS=350
CHUNK_OVERLAP_CHARS=80
PRICE_INPUT_PER_MTOK={"claude-haiku-4-5-20251001": 1.0}
PRICE_OUTPUT_PER_MTOK={"claude-haiku-4-5-20251001": 5.0}
```

Verificá los precios reales en claude.com/pricing antes de dejarlos fijos.

En la configuración del build, pasá `PRELOAD_EMBEDDINGS=true` como argumento.
Agranda la imagen pero evita que el primer request espere una descarga de
450 MB.

Cuando termine el despliegue:

```bash
curl https://tu-api.up.railway.app/health
```

Esperás `pgvector: true` y `mock_llm: false`.

## 4. Datos de la demo

Una demo vacía no demuestra nada. Cargá los fixtures:

```bash
python scripts/seed_demo.py --api https://tu-api.up.railway.app
```

Sube las 12 facturas, espera a que se procesen e imprime la cola de revisión.
Es idempotente: la deduplicación por hash evita reprocesar si lo corrés dos
veces.

Cuesta unos cinco centavos en tokens.

## 5. Frontend

En Vercel, importá el repositorio y poné `frontend` como directorio raíz.

```
NEXT_PUBLIC_API_URL=https://tu-api.up.railway.app
```

Después del primer despliegue, volvé a Railway y actualizá `CORS_ORIGINS` con
el dominio real que te asignó Vercel. Es el paso que más se olvida: sin él, el
sitio carga pero ninguna llamada a la API funciona y la consola del navegador
se llena de errores de CORS.

---

## Verificación

```bash
API=https://tu-api.up.railway.app

curl -s $API/health | python3 -m json.tool
curl -s "$API/documents?limit=20" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['items']), 'documentos')"
curl -s $API/review/queue | python3 -m json.tool
curl -s $API/invoices/summary | python3 -m json.tool
```

Y en el navegador: abrí el frontend, entrá a `hard_01.jpg`, hacé clic en
distintos campos y comprobá que el recuadro se mueve sobre la imagen.

## Costo

Neon, Upstash y Vercel entran en sus capas gratuitas a esta escala. Railway
cobra por memoria y tiempo de ejecución; con el modelo de embeddings cargado
en dos procesos son entre 1 y 1.5 GB, lo que ronda unos pocos dólares al mes.

Para bajarlo: `EMBEDDING_MODEL=intfloat/multilingual-e5-small` con
`EMBEDDING_DIM=384` usa bastante menos memoria, pero cambia la dimensión del
vector y exige una migración de la columna `embedding` y reindexar todo.

Cuando ya no necesites la demo, pausá el servicio.

## Decisiones y limitaciones

**API y worker corren en el mismo contenedor.** Ambos necesitan el mismo
`/data` y los volúmenes de los PaaS se montan en un solo servicio. En local
siguen separados vía docker-compose. A volumen real, lo correcto sería mover
los archivos a almacenamiento de objetos y separarlos de nuevo, lo que además
permitiría escalar el worker de forma independiente.

**El almacenamiento es un volumen, no un bucket.** Funciona para una demo y
falla en cuanto haya más de una instancia: cada una vería sus propios
archivos.

**No hay autenticación.** Cualquiera con el enlace puede subir documentos y
leer los que hay. Es deliberado para una demo pública; en un despliegue real
haría falta autenticación y aislamiento por organización.
