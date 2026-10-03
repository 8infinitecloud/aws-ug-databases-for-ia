# Costos del workshop

> Precios de referencia en USD, región `us-east-1`, revisados 2026-10.
> Los precios de AWS cambian: verifica en la
> [calculadora oficial](https://calculator.aws/) antes de asumir nada.

**Resumen: hacer el workshop completo en una sesión de ~2 horas cuesta menos de
$1. Olvidarte del cleanup cuesta unos $5 al día.**

---

## Coste por lab

### El core de pagos (`infra/payments`)

Es lo primero que se despliega: todos los labs leen de él.

| Servicio | Configuración | $/hora | 2 h |
|----------|--------------|--------|-----|
| EC2 (PostgreSQL "on-premises") | `t4g.small` + 20 GB gp3 | ~$0.019 | ~$0.04 |
| Fargate (API de adquirencia) | 0.5 vCPU + 1 GB | ~$0.025 | ~$0.05 |
| ALB interno | 1 | ~$0.0225 | ~$0.045 |
| API Gateway HTTP API | $1 por millón de peticiones | — | ~$0 |
| ECR, Secrets Manager, S3, CloudWatch | | — | < $0.01 |
| **Total core** | | **~$0.07/h** | **~$0.14** |

> Antes era Aurora Serverless v2 (mín. 0,5 ACU, ~$0.06/h solo la base): el core
> en EC2 cuesta un tercio menos y es lo que justifica DMS con CDC (ADR-024).

### Lab 1 — Ingestion + Data Lake

| Servicio | Configuración | $/hora | 2 h |
|----------|--------------|--------|-----|
| DMS Replication Instance | `dms.t3.small`, 50 GB | ~$0.035 | ~$0.07 |
| S3 | < 1 GB + requests | — | < $0.01 |
| **Total Lab 1** | | **~$0.04/h** | **~$0.08** |

> **Con CDC, DMS está encendido todo el tiempo**, haya pagos o no: es ~$0.85/día
> si se queda corriendo. Y si se destruye sin borrar su slot de replicación, el
> coste deja de ser dinero: el WAL retenido llena el disco del core. El
> `cleanup.sh` del Lab 1 hace las dos cosas.

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
| S3 | corpus de 11 documentos | — | < $0.01 |
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

### Lab 4 — Streaming + Insight Online

| Servicio | Uso típico | Coste |
|----------|-----------|-------|
| IoT Core | mensajes de pago + latidos cada 30 s de 9 terminales, 2 h | < $0.01 |
| Lambda (scoring) | unos miles de invocaciones | ~$0 (nivel gratuito) |
| DynamoDB | on-demand | < $0.01 |
| Kinesis Data Firehose | < 10 MB de telemetría | < $0.01 |
| **Total Lab 4** | | **~$0.01** |

> **En reposo el Lab 4 cuesta $0**: todo es por uso.

---

## Escenarios

### Workshop completo en una sesión (~2 h)

| Concepto | Coste |
|----------|-------|
| Core de pagos (2 h) | ~$0.14 |
| Lab 1 (2 h) | ~$0.08 |
| Lab 2 | ~$0.01 |
| Lab 3 (2 h) | ~$0.25 |
| Lab 4 | ~$0.01 |
| Bedrock | ~$0.05 |
| Backend de Terraform (S3 + DynamoDB) | < $0.01 |
| **Total** | **~$0.55** |

### Si te olvidas del cleanup

| Periodo | Core | Lab 1 | Lab 3 | Total |
|---------|------|-------|-------|-------|
| 1 día | ~$1.60 | ~$0.85 | ~$2.86 | **~$5.30** |
| 1 semana | ~$11 | ~$6 | ~$20 | **~$37** |
| 1 mes | ~$48 | ~$26 | ~$86 | **~$160** |

> Aurora Serverless v2 (Lab 3) no baja de 0.5 ACU: **cobra aunque nadie la use.**
> ElastiCache, EC2, el ALB del core y la instancia de DMS tampoco tienen escala a
> cero.

### Hacer los labs por separado, en días distintos

Es perfectamente válido y más barato si vas con calma: destruye cada lab al
acabarlo. El Lab 1 necesita el core; el Lab 2 y el Lab 4, el bucket del Lab 1; el
Lab 3 es independiente.

---

## Cómo no gastar dinero

### 1. Cleanup al terminar, en este orden

```bash
bash labs/04-streaming-online-insight/scripts/cleanup.sh
bash labs/02-transformation-quality-catalog/scripts/cleanup.sh
bash labs/01-ingestion-data-lake/scripts/cleanup.sh
bash labs/03-data-to-ai/infrastructure/cleanup.sh
terraform -chdir=infra/payments destroy
```

Los Labs 4 y 2 antes del 1: escriben en su bucket. El 1 antes que el core: su
cleanup borra el slot de replicación en la base del core, así que la necesita
viva.

### 2. Comprobar que no queda nada

```bash
for M in payments lab1 lab2 lab3 lab4; do
  echo -n "infra/$M: "
  terraform -chdir="infra/$M" state list 2>/dev/null | wc -l
done
```

Todos deben dar `0`.

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

Estos son los que duelen si se quedan encendidos:

- [RDS](https://console.aws.amazon.com/rds/) — el cluster Aurora del Lab 3
- [DMS](https://console.aws.amazon.com/dms/) — replication instances
- [ElastiCache](https://console.aws.amazon.com/elasticache/) — clusters Redis
- [EC2](https://console.aws.amazon.com/ec2/) — la base del core y el servidor del Lab 3
- [Load Balancers](https://console.aws.amazon.com/ec2/home#LoadBalancers) — el ALB del core

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

- ElastiCache `cache.t3.micro`: 750 h/mes
- EC2 `t3.micro`: 750 h/mes — **pero el Lab 3 usa `t3.medium`, que no entra**
- DynamoDB: 25 GB y 25 WCU/RCU
- S3: 5 GB

**DMS, Aurora Serverless v2, Glue y Bedrock no tienen nivel gratuito.** La
`t4g.small` del core tampoco (en algunas cuentas AWS ofrece una prueba gratuita
de `t4g.small`; compruébalo en la tuya).
