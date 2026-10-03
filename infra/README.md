# Infraestructura

```
infra/
├── bootstrap/   ← META: el backend de Terraform. Se ejecuta UNA vez.
├── payments/    ← EL CORE: el sistema transaccional. Todo orbita aquí.
├── lab1/        ← Ingesta: del core al data lake
├── lab2/        ← Transformación, calidad, catálogo y Athena
├── lab3/        ← IA sobre los datos curados
└── lab4/        ← Streaming: scoring de riesgo online
```

---

## Tres niveles, y por qué están al mismo nivel de directorio

Es una pregunta razonable, porque los tres niveles son distintos:

| Nivel | Módulo | Qué es |
|-------|--------|--------|
| **Meta** | `bootstrap/` | Infraestructura **para Terraform**, no para la aplicación |
| **Core** | `payments/` | El sistema transaccional. El origen del dato |
| **Soluciones** | `lab1/`…`lab4/` | Lo que se construye **sobre** el core |

### ¿Por qué `bootstrap/` no va dentro de `payments/`?

Porque su `backend.hcl` lo consumen **los cuatro módulos**, el helper
`scripts/lib/tf.sh` y dos workflows. Si viviera dentro de `payments/`, entonces
los cuatro labs dependerían de payments **solo para tener dónde guardar su
state** — una dependencia falsa que además invertiría el orden de arranque.

El state bucket no es una preocupación de pagos: es una preocupación de Terraform.
Está un nivel por debajo de todo lo demás, y por eso es el primero que se aplica y
el único que sobrevive al cleanup.

---

## El grafo de dependencias

```
bootstrap                 (todos necesitan su backend.hcl)
    │
    ▼
payments  ◀── EL CORE: PostgreSQL en EC2 ("on-premises"), Fargate, API Gateway
    │
    ▼
  lab1    ── DMS full load + CDC desde el core ──▶ S3 raw/acquiring/  (+ raw/legacy/)
    │
    ▼
  lab2    ── Glue: raw/ ──▶ curated/ + Athena sobre el catálogo

  lab3    ── independiente: su propio Aurora con pgvector para el RAG

  lab4    ── IoT Rule '+/events'    ──▶ Lambda ──▶ DynamoDB      (reaccionar, ms)
             IoT Rule '+/telemetry' ──▶ Firehose ──▶ S3 raw/telemetry/  (recordar, 60 s)
                                    └─▶ DynamoDB terminal-state  (vigilar)
```

Los pagos llegan al lago **por un solo camino**: el CDC del Lab 1, segundos
después de cada cambio. El Lab 4 ya no los manda por Firehose (era un duplicado
con otra forma); Firehose lleva la telemetría de los terminales, que no está en
ninguna base. Ver ADR-024 y ADR-026.

| Módulo | Lee el state de | Por qué |
|--------|-----------------|---------|
| `payments` | — | Es el core: no depende de nada |
| `lab1` | **`payments`** | VPC, subnets, IP de la base del core y su secreto |
| `lab2` | `lab1` | El bucket del data lake |
| `lab3` | — | Tiene su propia infraestructura para el vector store |
| `lab4` | `lab1` | El bucket del data lake, donde Firehose aterriza la telemetría |

> `lab3` es independiente a propósito: si el core falla en la charla, el Lab 3
> arranca igual. Ver ADR-016.

---

## Orden de despliegue

```bash
export TF_VAR_db_password='CambiaEstaPassword2026!'

# 1. Una vez por cuenta AWS
cd infra/bootstrap && terraform init && terraform apply

# 2. El core (~10 min): infra + imagen del backend, y la base arranca
#    sola con su esquema. Lo hace todo scripts/deploy_core.sh
cd ../.. && ./scripts/deploy_core.sh && cd infra

# 4. Los labs
cd lab1 && terraform init -backend-config=../backend.hcl && terraform apply
cd ../lab2 && terraform init -backend-config=../backend.hcl && terraform apply
cd ../lab3 && terraform init -backend-config=../backend.hcl && terraform apply
cd ../lab4 && terraform init -backend-config=../backend.hcl && terraform apply
```

Los scripts de cada lab (`labs/*/scripts/setup.sh`) hacen los pasos 4 y siguientes,
y comprueban que el core exista antes de empezar.

---

## Orden de destrucción: inverso, y con un matiz

```bash
cd infra/lab4 && terraform destroy     # primero: libera las reglas de IoT, y su
                                       # Firehose escribe en el bucket del lab1
cd ../lab2    && terraform destroy     # antes que lab1: escribe en su bucket
cd ../lab1    && terraform destroy     # antes que payments: sus ENIs de DMS
                                       # bloquearían el borrado de las subnets
# ...y borra el slot de replicación del core, que destroy no ve:
# labs/01-ingestion-data-lake/scripts/cleanup.sh lo hace por ti
cd ../lab3    && terraform destroy     # independiente, en cualquier momento
cd ../payments && terraform destroy
```

`bootstrap` **no se destruye**: el bucket tiene `prevent_destroy` porque contiene
el state de todo lo demás. Cuesta céntimos al mes.

---

## Por qué el state va remoto

El `terraform.tfstate` describe toda la infraestructura **y guarda las contraseñas
de las bases en claro**. Nunca se commitea (está en `.gitignore`), y vive en S3 por dos
razones:

1. La demo se despliega desde dos sitios — tu portátil y GitHub Actions. Con state
   local, el workflow arrancaría de cero e intentaría recrear lo que ya existe.
2. La tabla DynamoDB de locks evita que dos `apply` simultáneos lo corrompan.

`infra/backend.hcl` lo genera `bootstrap` y lleva tu account id, así que también
está en `.gitignore`. Los workflows lo reconstruyen desde la identidad que asumen
por OIDC.

---

## Coste

| Módulo | En reposo | Notas |
|--------|-----------|-------|
| `bootstrap` | ~$0.01/mes | S3 + DynamoDB on-demand |
| `payments` | **~$0.07/hora** | La EC2 de la base, Fargate y el ALB no bajan a cero |
| `lab1` | **~$0.04/hora** | La instancia de DMS: con CDC está siempre encendida |
| `lab2` | **$0** | Glue solo cobra cuando corre |
| `lab3` | **~$0.12/hora** | Aurora, Redis y EC2 |

Detalle en [COST_ESTIMATE.md](../COST_ESTIMATE.md).
