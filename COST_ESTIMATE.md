# Costos del workshop

> Precios de referencia en USD, región `us-east-1`, revisados 2026-09.
> Los precios de AWS cambian: verifica en la
> [calculadora oficial](https://calculator.aws/) antes de asumir nada.

**Resumen: hacer el workshop completo en una sesión de ~2 horas cuesta menos de
$1. Olvidarte del cleanup cuesta ~$20/día.**

---

## Coste por lab

### Lab 1 — Ingestion + Data Lake

| Servicio | Configuración | $/hora | 2 h |
|----------|--------------|--------|-----|
| RDS MySQL | `db.t3.micro`, 20 GB gp2 | ~$0.017 | ~$0.034 |
| DMS Replication Instance | `dms.t3.medium`, 50 GB | ~$0.068 | ~$0.136 |
| S3 | < 1 GB + requests | — | < $0.01 |
| Secrets Manager | 1 secret | ~$0.0005 | < $0.01 |
| **Total Lab 1** | | **~$0.085/h** | **~$0.18** |

> DMS es el más caro y el que se olvida más fácil, porque no aparece en la
> consola de RDS. Es ~$1.63/día si se queda corriendo.

### Lab 2 — Transformation + Quality + Catalog

| Servicio | Configuración | Coste |
|----------|--------------|-------|
| Glue Python Shell Job | 0.0625 DPU × ~3 min | ~$0.0006 por ejecución |
| Glue Crawler | 1 crawl de < 1 min | ~$0.0007 |
| Glue Data Catalog | < 1M objetos | $0 (nivel gratuito) |
| S3 (curated + rejected + reports) | < 1 GB | < $0.01 |
| **Total Lab 2** | | **~$0.01** |

> **En reposo el Lab 2 cuesta $0.** No hay nada encendido: Glue solo cobra
> cuando un job corre. Es el lab que puedes dejar desplegado sin miedo.

### Lab 3 — Data to AI (RAG)

| Servicio | Configuración | $/hora | 2 h |
|----------|--------------|--------|-----|
| Aurora PostgreSQL Serverless v2 | mín. 0.5 ACU | ~$0.06 | ~$0.12 |
| Aurora storage | ~1 GB | — | < $0.01 |
| ElastiCache Redis | `cache.t3.micro` | ~$0.017 | ~$0.034 |
| EC2 App Server | `t3.medium` | ~$0.042 | ~$0.084 |
| DynamoDB | on-demand, < 1K ops | — | < $0.01 |
| S3 | corpus de 8 documentos | — | < $0.01 |
| **Total Lab 3** | | **~$0.12/h** | **~$0.25** |

#### Bedrock (se paga por uso, no por tiempo)

| Modelo | Uso típico del lab | Coste |
|--------|-------------------|-------|
| Titan Embeddings V2 | ~50K tokens de ingesta del corpus | ~$0.001 |
| Titan Embeddings V2 | ~20 queries × 50 tokens | < $0.001 |
| Nova Pro | ~20 queries × (2K in + 500 out) | ~$0.04 |
| **Total Bedrock** | | **~$0.05** |

> Nova Pro es ~8× más barato que Claude Sonnet para este uso. Por eso es el
> modelo por defecto de un lab que se repite muchas veces.

---

## Escenarios

### Workshop completo en una sesión (~2 h)

| Concepto | Coste |
|----------|-------|
| Lab 1 (2 h) | ~$0.18 |
| Lab 2 | ~$0.01 |
| Lab 3 (2 h) | ~$0.25 |
| Bedrock | ~$0.05 |
| Backend de Terraform (S3 + DynamoDB) | < $0.01 |
| **Total** | **~$0.50** |

### Si te olvidas del cleanup

| Periodo | Lab 1 | Lab 3 | Total |
|---------|-------|-------|-------|
| 1 día | ~$2.04 | ~$2.86 | **~$4.90** |
| 1 semana | ~$14 | ~$20 | **~$34** |
| 1 mes | ~$61 | ~$86 | **~$147** |

> Aurora Serverless v2 no baja de 0.5 ACU: **cobra aunque nadie la use.**
> ElastiCache y EC2 tampoco tienen escala a cero.

### Hacer los labs por separado, en días distintos

Es perfectamente válido y más barato si vas con calma: destruye cada lab al
acabarlo. Los tres son independientes salvo que el Lab 2 necesita el Lab 1
desplegado.

---

## Cómo no gastar dinero

### 1. Cleanup al terminar, en este orden

```bash
bash labs/02-transformation-quality-catalog/scripts/cleanup.sh
bash labs/01-ingestion-data-lake/scripts/cleanup.sh
bash labs/03-data-to-ai/infrastructure/cleanup.sh
```

El Lab 2 antes del Lab 1: el Lab 2 escribe en el bucket que crea el Lab 1.

### 2. Comprobar que no queda nada

```bash
for M in lab1 lab2 lab3; do
  echo -n "infra/$M: "
  terraform -chdir="infra/$M" state list 2>/dev/null | wc -l
done
```

Los tres deben dar `0`.

### 3. Poner un presupuesto con alerta

Antes de empezar, y lleva 2 minutos:

```bash
aws budgets create-budget \
  --account-id "$(aws sts get-caller-identity --query Account --output text)" \
  --budget '{
    "BudgetName": "workshop-datos-a-ia",
    "BudgetLimit": {"Amount": "10", "Unit": "USD"},
    "TimeUnit": "MONTHLY",
    "BudgetType": "COST"
  }'
```

O desde la [consola de Budgets](https://console.aws.amazon.com/billing/home#/budgets),
que además te deja añadir el aviso por email.

### 4. Revisar a mano lo que cuesta

Estos tres servicios son los que duelen si se quedan encendidos:

- [RDS](https://console.aws.amazon.com/rds/) — instancias y clusters Aurora
- [DMS](https://console.aws.amazon.com/dms/) — replication instances
- [ElastiCache](https://console.aws.amazon.com/elasticache/) — clusters Redis

---

## Lo que sobrevive al cleanup (y por qué)

| Recurso | Coste | Por qué se queda |
|---------|-------|------------------|
| Bucket del estado de Terraform | ~$0.01/mes | Es donde vive el estado de todo lo demás |
| Tabla DynamoDB de locks | $0 sin uso | Va con el bucket del estado |

Ambos tienen `prevent_destroy`. Si quieres borrarlos de verdad, hay que quitar
esa protección a mano — está puesta para que un `destroy` accidental no te deje
sin el estado.

---

## Nivel gratuito

Si tu cuenta es nueva (< 12 meses), entran en el nivel gratuito:

- RDS `db.t3.micro`: 750 h/mes
- ElastiCache `cache.t3.micro`: 750 h/mes
- EC2 `t3.micro`: 750 h/mes — **pero el Lab 3 usa `t3.medium`, que no entra**
- DynamoDB: 25 GB y 25 WCU/RCU
- S3: 5 GB

**DMS, Aurora Serverless v2, Glue y Bedrock no tienen nivel gratuito.**
