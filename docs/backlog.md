# Backlog de mejoras

Lo que se podría añadir, y si merece la pena **ahora**. El criterio es uno: el
taller va **de los datos a la IA**, no de ingeniería de pagos. Una pieza nueva
entra solo si mejora la historia de los datos más de lo que cuesta construirla,
explicarla y ensayarla; una pieza sin ensayar es un riesgo en vivo.

| Prioridad | Significa |
|---|---|
| **Ahora** | Aporta a la demo y es pequeño. Se hace antes de la charla |
| **Después** | Correcto y útil, pero no cambia lo que la charla enseña |
| **Mencionar** | Mejor como una frase en la diapositiva de cierre que como código |

---

## Backend de pagos (`payments/`)

| Mejora | Prioridad | Por qué |
|---|---|---|
| **Devoluciones** (`POST /v1/refunds`) | ✅ **Hecho** | Es el mejor ejemplo de por qué CDC y no un incremental: un cobro de ayer que hoy pasa a devuelto es un `U` sobre una fila antigua que una consulta por fecha de creación nunca vería. `REFUNDED` ya está en las reglas de calidad y el corpus habla del ratio de devoluciones, pero hoy nada genera una |
| Captura manual (`capture_method = manual` + `/capture`) | Después | La preautorización del hotel. Correcta, pero el QR pendiente → pagado ya enseña el `U` |
| Antifraude síncrono antes de autorizar (al estilo Radar) | Mencionar | Toca el camino del cobro y se solapa con el Lab 4. En la charla: *"antes de esto hay un Radar que bloquea en el propio cobro; el Lab 4 ve patrones entre operaciones"*. `charges.risk_score` ya existe para cuando se haga |
| Contracargos (`/v1/disputes`, evidencia) | Después | El corpus los menciona; el flujo es largo (días) y no cabe en una demo |
| API de lectura del comercio (`GET /v1/charges`, `/v1/balance_transactions`) | Después | `v_merchant_operations` y el permiso `read:charges` ya existen |
| Liquidaciones (`/v1/payouts`) | Después | Completan el ciclo del dinero, no la historia de los datos |
| Worker de entrega de webhooks (firma + reintentos) | Después | Las tablas y los eventos existen; la entrega no aporta datos nuevos al lago |
| `PATCH /v1/intents/{ref}` (update) y `GET /v1/intents` (list) | Después | Completan la API de Stripe; nadie los usa en la demo |
| El terminal se autentica con su certificado (mTLS con el ATECC608) | Mencionar | Hoy el POS se identifica solo por su `terminal_id`. Es la concesión de seguridad más visible del backend |
| El Core2 cancela por API el QR abandonado | Después | Hoy cancela solo en pantalla; la intención queda `REQUIRES_CONFIRMATION`. Exige regrabar y probar el firmware |
| Desactivar `/docs` y `/openapi.json` | Después | Públicos por la ruta comodín de API Gateway. Útiles en un taller; fuera en producción |
| Páginas `/pay` y `/3ds` en S3 + CloudFront (mismo dominio) | Después | Lo correcto en producción; en la demo es un servicio más sin lección de datos (ADR-021) |

## Plataforma de datos

| Mejora | Prioridad | Por qué |
|---|---|---|
| Tablas de Athena sobre `raw/acquiring/` (los deltas en crudo) | Después | Contar los pagos de la sala directamente en `raw/` mientras llegan. Hoy se ven tras el Glue del Lab 2 |
| Iceberg en `curated/` y *partition evolution* | Después | Cambiar la clave de partición sin reescribir la historia (planteado al revisar el Lab 2) |
| Glue con *job bookmarks* sobre los deltas | Después | Hoy el job relee todo `raw/`: a este volumen da igual |
| Agente con Athena como herramienta (text-to-SQL) | Mencionar | ADR-027: la cadena RAG responde lo que el taller pregunta |
| Redshift | Descartado | ADR-022 |

## Operación

| Mejora | Prioridad | Por qué |
|---|---|---|
| Probar la telemetría del firmware en el Core2 | **Ahora** | Compila, pero nunca corrió en el dispositivo |
| Rol OIDC y job de deploy de `payments` en GitHub Actions | Después | `scripts/deploy_core.sh` cubre el despliegue manual |
| Usuario de DMS solo con `REPLICATION` + `SELECT` | Después | Hoy usa el `SUPERUSER` de la base (ver `docs/well-architected.md`) |
