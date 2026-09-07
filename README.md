# DocuFlow

Extracción estructurada y búsqueda semántica sobre facturas escaneadas. Subís un
PDF o la foto de un recibo; el sistema hace OCR, extrae los campos a tablas de
Postgres con un score de confianza por campo, marca los dudosos para revisión
humana, y te deja preguntarle en lenguaje natural con citas a la página exacta.

**[Demo en vivo](https://docu-flow-nine-xi.vercel.app)** · [API](https://docu-flow-production.up.railway.app/docs)

La demo trae 12 facturas ya procesadas. Empezá por `hard_01.jpg` en la cola de
revisión: es una foto degradada a propósito, y ahí se ve el flujo completo de
corrección humana.

<!-- TODO: GIF de 15 segundos de la pantalla de revisión, grabado en la demo. -->

---

## El problema

Una PyME recibe facturas en tres formatos a la vez: PDFs de facturación
electrónica, PDFs escaneados y fotos tomadas con el celular. Pasarlas al sistema
contable es digitación manual.

Automatizar la digitación es fácil de empezar y difícil de terminar, porque el
modelo se equivoca y el OCR también. DocuFlow asume eso desde el diseño: cada
campo extraído lleva su confianza y su procedencia, y los que no superan el
umbral entran a una cola de revisión en lugar de contaminar la base.

## Arquitectura

```
Documentos (PDF, JPG, PNG, TIFF)
        ↓
Worker asíncrono (Celery)     OCR → extracción con LLM → embeddings
        ↓
PostgreSQL + pgvector         tablas normalizadas + búsqueda híbrida
        ↓
API (FastAPI)                 RAG con citas, agregados SQL, trazas de costo
        ↓
Frontend (Next.js)            revisión humana con resaltado sobre la imagen
```

El sistema tiene dos mitades que responden preguntas distintas:

**Preguntas semánticas** ("qué le compré a Cuscatlán") van por búsqueda híbrida
y RAG con citas verificadas.

**Preguntas de agregación** ("cuánto pagué de IVA") van por SQL sobre los campos
extraídos. Un recuperador top-k solo ve una muestra del corpus, así que el RAG
responde con seguridad sobre un subconjunto. Medido: el RAG contestó $1,298.96
sumando las 7 facturas que le tocó ver; el total real sobre las 12 es
**$1,881.06**, un 31% más. El endpoint de chat detecta preguntas de agregación y
declara el alcance de su respuesta en lugar de fingir que vio todo.

## Stack

| Capa | Tecnología | Por qué |
|---|---|---|
| Backend | Python 3.11, FastAPI, Pydantic | Validación de schema en el borde y en la salida del LLM |
| Cola | Celery + Redis | El OCR tarda segundos; no puede bloquear un request HTTP |
| Base de datos | PostgreSQL 16 + pgvector | Datos estructurados y vectores en un solo motor |
| OCR | Tesseract (`spa+eng`) con preprocesamiento OpenCV | Umbral adaptativo para fotos con sombra |
| Embeddings | API de Voyage (`voyage-4-lite`) | Ver "Decisiones" |
| Extracción | Claude Haiku 4.5 | Alto volumen, tarea acotada |
| Respuestas RAG | Claude Sonnet 5 | Bajo volumen, requiere razonar |
| Frontend | Next.js, TypeScript, Tailwind | — |

## Resultados

Medido sobre 12 facturas sintéticas con etiquetas de referencia
(`fixtures/ground_truth.json`), 120 campos comparados.

| Métrica | Valor |
|---|---|
| Exactitud cuando el sistema responde | **93.3%** |
| Cobertura global | 82.5% |
| Abstención (campos donde dice "no sé") | 12% |
| **Fallos silenciosos** (error con confianza ≥ 0.85) | **2 de 120 campos (1.7%)** |
| Costo por documento | $0.0045 |
| Costo proyectado por 1000 documentos | $4.51 |

El costo corresponde a una corrida limpia: una llamada de extracción por
documento, sin reintentos ni consultas de chat. `GET /stats/cost` en la demo
muestra un número mayor porque acumula reintentos y preguntas al chat en el
mismo denominador.

El número que más importa es el último de los de exactitud: **1.7% de fallos
silenciosos**. Es la tasa a la que el sistema se equivoca sin avisar. Todo lo
demás lo agarra un humano en la cola de revisión.

### Degradación por calidad del documento

| Dificultad | Cobertura | Exactitud si responde | Abstención |
|---|---|---|---|
| `clean` (PDF vectorial) | 100% | 100% | 0% |
| `scanned` (escáner, ruido, 1° de giro) | 100% | 100% | 0% |
| `photo` (foto de celular, sombra) | 93% | 93% | 0% |
| `hard` (fotocopia gastada, 7° de giro) | 5% | 20% | 75% |

La fila `hard` parece un desastre y no lo es del todo: de 20 campos, el modelo se
abstuvo en 15. No inventó — reconoció que no podía leer la imagen. Ese es el
comportamiento buscado, pero también indica que documentos con OCR por debajo de
60 de confianza deberían rechazarse en la entrada en lugar de consumir tokens.

### Contra una línea base sin LLM

`scripts/generate_fixtures.py` incluye un extractor por expresiones regulares que
además funciona como modo mock. Comparado con el modelo:

| Campo | Regex | LLM |
|---|---|---|
| `invoice_number` | 83% | 100% |
| `issue_date` | 83% | 100% |
| `vendor_tax_id` | 83% | 83% |
| **`total`** | **25%** | **91%** |
| `subtotal`, `tax_amount`, `buyer_name` | no lo intenta | 100%, 100%, 80% |

Las regex empatan en campos con formato fijo y colapsan en el total: agarran la
cifra más grande del documento y aciertan una de cada cuatro veces. El modelo
cuesta dinero solo donde aporta.

Reproducir:

```bash
python scripts/evaluate.py --api https://docu-flow-production.up.railway.app
```

## Decisiones técnicas

### Confianza combinada de tres señales

Un LLM reporta 0.95 con la misma seguridad cuando acierta que cuando alucina. La
confianza de cada campo se calcula cruzando tres señales independientes y
quedándose con la más débil:

1. **La confianza autorreportada del modelo.** Mal calibrada por sí sola.
2. **Anclaje en el OCR.** Al modelo se le exige devolver el fragmento literal de
   donde sacó cada valor, y ese fragmento se busca entre las palabras que produjo
   Tesseract. Si no aparece, el valor se lo inventó. Verificado: valores reales
   anclan con confianza 0.92–0.96; valores inventados a mano quedan en 0.
3. **Validación por reglas.** Si `subtotal + IVA ≠ total`, los tres campos pierden
   confianza aunque el modelo jure lo contrario. Un total inconsistente reportado
   con 0.95 termina en **0.158** y entra a revisión.

El anclaje además da el bounding box gratis: ya sabemos qué palabras respaldan el
valor, así que el frontend puede resaltar la zona exacta de la página.

**Limitación conocida:** el anclaje protege de alucinaciones, no de errores de OCR
con alta confianza. Los 2 fallos silenciosos son Tesseract leyendo "GA" como "OA"
en un nombre. El modelo transcribió fielmente. La solución sería pasarle también
la imagen al modelo para que contraste.

### Búsqueda híbrida en Postgres, no una vector DB

`pgvector` con índice HNSW para la parte densa, más el full-text nativo de
Postgres (`tsvector` en español, índice GIN) para la léxica, fusionados con
Reciprocal Rank Fusion.

RRF fusiona por **posición**, no por puntaje: una distancia coseno y un `ts_rank`
no son magnitudes comparables, y normalizarlas exige calibrar pesos que cambian
con cada corpus.

Por qué las dos ramas, medido: buscando el número de factura `FAC-2026-1117`, la
rama léxica lo puso en posición 1 y la vectorial en la 7, enterrado bajo cuatro
documentos irrelevantes. Los embeddings son malos con identificadores porque
`FAC-2026-1117` y `FAC-2026-9882` son casi el mismo vector. Cuando ambas ramas
coinciden, el score de RRF se duplica (0.0313 contra 0.0164), que es exactamente
la señal de fusión funcionando.

*Trade-off:* por encima de unos pocos millones de fragmentos, un motor dedicado
escalaría mejor. A la escala de una PyME no se justifica la dependencia extra.

### El tamaño de fragmento se descubrió midiendo

El valor inicial de 900 caracteres hacía que cada factura cupiera en un solo
fragmento. La búsqueda semántica devolvía encabezados en lugar de la línea del
IVA, porque el embedding de una factura entera queda dominado por el nombre del
proveedor. Bajarlo a 350 alinea los fragmentos con la estructura real del
documento — encabezado, detalle, totales — y la línea del IVA pasa a tener su
propio vector.

Los cortes se hacen en límites de línea: partir a la mitad de
`IVA 13%: USD 401.57` haría que ese dato no se encuentre por ninguno de los dos
lados.

### Embeddings: por qué cambió la decisión tres veces

Se empezó con un modelo local (`multilingual-e5-base`) para que el costo no
escalara con el volumen. Al desplegar, el contenedor recibía SIGKILL: el modelo
con torch cargado ocupa unos 760 MB residentes, por encima del límite del plan
gratuito.

Reducir a `e5-small` bajó el pico a 762 MB — insuficiente, porque los modelos
multilingües cargan un vocabulario de 250.000 tokens y esa matriz domina el
tamaño sin importar cuántas capas tenga el modelo.

La solución fue mover los embeddings a la API de Voyage y sacar torch de la
imagen: de ~2 GB a 747 MB de imagen, y de ~760 MB a ~200 MB de memoria residente.
La recuperación además mejoró de forma visible.

La capa gratuita de Voyage limita a 3 peticiones por minuto, así que se agregó un
caché LRU de vectores de consulta. Medido: la segunda vez que se repite una
consulta pasa de 435 ms a 30 ms.

La restricción real casi nunca es la que uno anticipa. El proveedor de embeddings
quedó detrás de una interfaz, así que el modelo local sigue disponible con
`EMBEDDING_PROVIDER=local`.

### La corrección humana se guarda aparte del valor original

`extracted_fields` conserva `value_text` (lo que dijo el modelo) y
`corrected_value` (lo que dijo el humano). El uso normal de la aplicación genera
un set de evaluación etiquetado sin pedirle a nadie que etiquete nada.

La corrección se propaga a la tabla `invoices`. Sin esa propagación aparece el
bug silencioso clásico: el usuario corrige, la interfaz muestra el valor bueno, y
los reportes siguen leyendo el valor malo de la otra tabla.

### Deduplicación de proveedores por trigramas

El OCR lee "Servicios Informaticos Pipil, S.A. de C.V." en un documento y
"Servicios informaticos Pipil, S.A." en otro. Agrupar por texto exacto parte el
mismo proveedor en dos y arruina cualquier reporte por proveedor.

`GET /invoices/vendors` agrupa por similitud de trigramas usando `pg_trgm`, y
expone las variantes fusionadas de cada grupo para que un humano pueda auditar la
decisión. El umbral es un parámetro con consecuencias de negocio: fusionar de más
mezcla el gasto de dos empresas distintas, fusionar de menos parte un proveedor en
dos.

*Limitación:* la asignación es voraz, no un clustering transitivo.

### Una fila por llamada al modelo

`llm_calls` guarda tokens, costo estimado y latencia de cada invocación. Permite
responder "¿cuánto cuesta procesar mil facturas al mes?" con un número medido en
lugar de una estimación, que es la primera pregunta de quien firma el cheque.

## Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| `POST` | `/documents` | Sube un archivo. Deduplica por sha256 |
| `GET` | `/documents` | Lista con estado del pipeline |
| `POST` | `/documents/{id}/reprocess` | Reencola sin resubir el archivo |
| `GET` | `/documents/{id}/pages/{n}` | Texto OCR y palabras con bounding box |
| `GET` | `/documents/{id}/fields` | Campos con confianza y procedencia |
| `PATCH` | `/fields/{id}` | Corrección humana, se propaga a `invoices` |
| `GET` | `/review/queue` | Documentos pendientes, los peores primero |
| `GET` | `/search` | Búsqueda híbrida, expone el rango de cada rama |
| `POST` | `/chat` | RAG con citas verificadas |
| `GET` | `/invoices/summary` | Agregados SQL sobre todas las facturas |
| `GET` | `/invoices/vendors` | Proveedores deduplicados |
| `GET` | `/stats/cost` | Costo, latencia y volumen de llamadas |

Documentación interactiva en `/docs`.

## Correr localmente

Requiere Docker.

```bash
cp .env.example .env      # poné tus API keys
docker compose up -d --build
docker compose exec api alembic upgrade head
python scripts/generate_fixtures.py
python scripts/seed_demo.py
```

`MOCK_LLM=true` (por defecto) corre el pipeline completo sin llamar al modelo ni
necesitar API key: usa el extractor heurístico, que además es la línea base de los
evals.

Los datos de prueba son sintéticos y reproducibles (`SEED = 20260907`). No hay
datos reales de ninguna empresa en este repositorio.

Para desplegar, ver [DEPLOY.md](DEPLOY.md).

## Qué haría con más tiempo

**Pasarle la imagen al modelo, no solo el texto OCR.** Es la causa de los 2 fallos
silenciosos: el modelo no puede detectar que Tesseract leyó mal si Tesseract
estaba seguro.

**Calibrar el umbral de revisión con datos.** Está fijado en 0.85 por criterio,
no por medición. Un monto de `hard_02` se leyó como 29.05 en lugar de 525.05 con
confianza 0.83: dos centésimas por debajo del corte. Con un set más grande se
podría elegir el umbral que minimice fallos silenciosos sin inundar de trabajo al
revisor.

**Rechazar documentos en la entrada.** Un documento con OCR por debajo de 60 de
confianza no debería consumir tokens; debería rebotar pidiendo una foto mejor.

**Barrido de tareas huérfanas al arrancar.** Si el worker se reinicia a mitad de
un documento, ese documento queda congelado en un estado intermedio para siempre.
Descubierto desplegando.

**Almacenamiento de objetos en lugar de un volumen.** Hoy API y worker corren en
el mismo contenedor porque comparten `/data`. Mover los archivos a S3 o R2
permitiría separarlos y escalar el worker de forma independiente.

**Afinar un extractor pequeño con las correcciones acumuladas.** La tabla
`extracted_fields` ya está generando el dataset.

**Autenticación y aislamiento por organización.** La demo es pública y sin login a
propósito. No es un despliegue real.

## Licencia

MIT
