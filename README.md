# DocuFlow

Extracción estructurada y búsqueda semántica sobre facturas y recibos escaneados.
Subes un PDF o una foto, el sistema hace OCR, extrae los campos a tablas de Postgres
con un score de confianza por campo, y te deja preguntarle en lenguaje natural con
citas a la página exacta del documento.

<!-- TODO: reemplazar por un GIF de 15s de la app en uso. Es lo primero que ve
     cualquiera que abra este repo. Grábalo con la demo desplegada, no en local. -->

**Demo:** <!-- TODO: URL --> · **Video (90s):** <!-- TODO: URL -->

---

## Qué resuelve

Una PyME recibe facturas en tres formatos a la vez: PDFs de facturación electrónica,
PDFs escaneados y fotos de recibos tomadas con el celular. Meterlas a un sistema
contable es trabajo manual de digitación. DocuFlow automatiza la digitación pero
asume que el modelo se equivoca: cada campo extraído lleva su confianza y su
procedencia, y los campos dudosos entran a una cola de revisión humana.

## Arquitectura

```
Documentos / APIs externas
        ↓
Worker asíncrono (Celery)   OCR → extracción con LLM → embeddings
        ↓
PostgreSQL + pgvector       tablas normalizadas + búsqueda híbrida
        ↓
API (FastAPI)               RAG con citas, evals, trazas de costo
        ↓
Frontend (Next.js + TS)     revisión humana, chat, dashboard
```

## Stack

| Capa | Tecnología | Por qué |
|---|---|---|
| Backend | Python 3.11, FastAPI, Pydantic | Validación de schema en el borde y en las respuestas del LLM |
| Cola | Celery + Redis | El OCR tarda segundos; no puede bloquear un request HTTP |
| Base de datos | PostgreSQL 16 + pgvector | Datos estructurados y vectores en un solo motor |
| OCR | Tesseract (`spa+eng`), con fallback a modelo de visión | Gratis para el 80% de los casos, modelo caro solo cuando hace falta |
| Embeddings | `multilingual-e5-base` local | Sin costo por token, funciona offline, multilingüe |
| LLM | Claude Haiku (extracción) + Sonnet (respuestas) | Modelo barato para volumen, modelo fuerte para razonar |
| Frontend | Next.js, TypeScript, Tailwind | — |

## Decisiones técnicas y trade-offs

**Búsqueda híbrida en Postgres, no una vector DB dedicada.**
Combinamos `pgvector` (similitud coseno, índice HNSW) con el full-text nativo de
Postgres (`tsvector` en español, índice GIN) y fusionamos con Reciprocal Rank Fusion.
La parte léxica es la que rescata números de factura y NITs, donde los embeddings
son malos. Evitamos una dependencia externa y mantenemos transaccionalidad entre
los datos estructurados y el índice.
*Trade-off:* por encima de unos pocos millones de chunks, un motor dedicado escalaría
mejor. A la escala de una PyME no se justifica.

**HNSW en vez de IVFFlat.**
IVFFlat necesita datos cargados para entrenar sus listas; HNSW se construye
incremental, que es lo correcto cuando el corpus crece continuamente.
*Trade-off:* más memoria y build más lento por vector.

**Extracción con schema y confianza por campo, no texto libre.**
El modelo devuelve JSON validado contra un schema de Pydantic. Cada campo lleva
confianza, número de página y bounding box. Por debajo del umbral, entra a revisión.
*Trade-off:* más tokens por documento que un prompt suelto, y más código.

**Todos los campos de `invoices` son nullable.**
Un recibo arrugado no trae NIT. Un `NULL` honesto es preferible a un valor
inventado por el modelo.

**Corrección humana guardada aparte del valor original.**
`extracted_fields` conserva `value_text` (lo que dijo el modelo) y `corrected_value`
(lo que dijo el humano). Eso convierte el uso normal de la app en un set de
evaluación etiquetado, sin trabajo adicional.

**Una fila en `llm_calls` por cada llamada al modelo.**
Tokens, costo estimado y latencia. Permite responder "¿cuánto cuesta procesar mil
facturas al mes?" con un número en vez de una estimación.

## Evaluación

<!-- TODO (paso 9): correr los evals y pegar la tabla real.
     Set de N documentos etiquetados a mano. Precisión por campo. -->

| Campo | Exactitud | n |
|---|---|---|
| `invoice_number` | — | — |
| `issue_date` | — | — |
| `vendor_name` | — | — |
| `total` | — | — |

Costo medio por documento: — · Latencia p50 / p95: —

## Correr localmente

Requiere Docker.

```bash
cp .env.example .env
docker compose up -d --build          # la primera vez tarda ~5 min
docker compose exec api alembic upgrade head
curl http://localhost:8000/health
```

Documentación interactiva de la API en http://localhost:8000/docs

`MOCK_LLM=true` (por defecto) corre el pipeline completo sin llamar al modelo ni
necesitar API key. Para usar el modelo real, pon tu key en `.env` y cambia a `false`.

## Qué haría con más tiempo

<!-- TODO: llenar al final. Esta sección se lee como madurez de ingeniería.
     Ideas: reintentos con backoff en el worker, particionado de chunks por tenant,
     caché de embeddings por hash de chunk, fine-tuning de un extractor pequeño
     con las correcciones humanas acumuladas. -->

## Licencia

MIT
