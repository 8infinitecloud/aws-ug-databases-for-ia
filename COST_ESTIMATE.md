# Estimación de Costos

> Precios en USD. Región: `us-east-1`. Actualizado: 2024-11.  
> Los precios pueden variar. Verificar en [AWS Pricing Calculator](https://calculator.aws/).

---

## Escenario 1: Laboratorio (Demo en Vivo — 2 horas)

| Servicio | Configuración | Costo Estimado |
|----------|--------------|----------------|
| **Amazon Bedrock** | Titan Embeddings: ~500K tokens de ingesta + 100 queries × 512 tokens | ~$0.03 |
| **Amazon Bedrock** | Claude Sonnet 3.5: 100 queries × ~2K tokens input + 500 tokens output | ~$0.35 |
| **Aurora PostgreSQL Serverless v2** | 0.5 ACU mínimo × 2 horas + 1 GB storage | ~$0.08 |
| **ElastiCache Redis** | cache.t3.micro × 2 horas | ~$0.03 |
| **Amazon DynamoDB** | On-demand, <1K lecturas/escrituras | ~$0.01 |
| **Amazon S3** | <1 GB storage + transferencia | ~$0.02 |
| **Total Lab (2h)** | | **~$0.52** |

> **Costo real dominante si olvidas hacer cleanup:** Aurora y ElastiCache siguen corriendo.  
> Costo diario si no eliminas: ~$15-25 USD/día.

---

## Escenario 2: Desarrollo y Pruebas (1 mes, equipo de 3)

| Servicio | Configuración | Costo/Mes |
|----------|--------------|-----------|
| **Amazon Bedrock — Embeddings** | 10M tokens/mes (reingesta + queries) | $2.00 |
| **Amazon Bedrock — Claude Sonnet 3.5** | 500K tokens input + 100K output/mes | $4.25 |
| **Aurora Serverless v2** | Promedio 1 ACU, 20 GB storage, 8h/día | ~$45 |
| **ElastiCache Redis** | cache.t3.micro (siempre encendido) | ~$12 |
| **DynamoDB** | On-demand, 100K R/W/mes | $0.25 |
| **S3** | 5 GB documents + transferencia | $0.15 |
| **Secrets Manager** | 2 secrets | $0.80 |
| **Total Dev/Mes** | | **~$64** |

---

## Escenario 3: Producción (empresa financiera mediana, 500 usuarios/día)

| Servicio | Configuración | Costo/Mes |
|----------|--------------|-----------|
| **Amazon Bedrock — Embeddings** | 50M tokens/mes | $10 |
| **Amazon Bedrock — Claude Sonnet 3.5** | 5M tokens input + 1M output/mes | $42.50 |
| **Aurora PostgreSQL Serverless v2** | Promedio 4 ACU, 100 GB storage, HA | ~$380 |
| **ElastiCache Redis** | cache.r7g.large (2 nodos, Multi-AZ) | ~$280 |
| **DynamoDB** | On-demand, 5M R/W/mes | $2.50 |
| **S3** | 50 GB + transferencia | $1.50 |
| **VPC, NAT Gateway** | 2 AZs, tráfico moderado | ~$65 |
| **CloudWatch Logs/Metrics** | Dashboards + alertas | ~$15 |
| **Secrets Manager** | 5 secrets | $2 |
| **Total Producción/Mes** | | **~$800** |

### Optimizaciones para Producción

| Optimización | Ahorro Estimado |
|-------------|-----------------|
| Usar **Titan Embeddings V2 con 256 dimensiones** en vez de 1024 | -60% costo embedding |
| **Caché semántico** (Redis) para queries similares | -30% costo LLM |
| **Aurora Provisioned** en vez de Serverless (si carga predecible) | -40% costo DB |
| **Bedrock Provisioned Throughput** (si >100K tokens/min) | -50% costo LLM |
| **S3 Intelligent-Tiering** para documentos históricos | -30% costo S3 |

---

## Notas sobre Precios

- **Amazon Bedrock:** Pago por token consumido; sin costo por hora encendido.
- **Aurora Serverless v2:** Se factura por ACU·hora + GB·mes. Escala a 0 ACU tras inactividad (pero no hay garantía de cuándo).
- **ElastiCache:** Se factura por hora de nodo aunque esté idle — apagar en entornos de dev.
- **DynamoDB:** On-demand es ideal para cargas variables como un lab; en producción con carga predecible usar **Provisioned**.

---

## Cleanup Cost Savings

Ejecutar `infrastructure/cleanup.sh` después del lab elimina Aurora y ElastiCache, los dos servicios más costosos.

```bash
# Tiempo estimado de cleanup: 5-10 minutos
cd infrastructure && ./cleanup.sh
```
