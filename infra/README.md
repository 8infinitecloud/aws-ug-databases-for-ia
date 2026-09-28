# Infraestructura

```
infra/
├── bootstrap/   ← META: el backend de Terraform. Se ejecuta UNA vez.
├── payments/    ← EL CORE: el sistema transaccional. Todo orbita aquí.
├── lab1/        ← Ingesta: del core al data lake
├── lab2/        ← Transformación, calidad y catálogo
└── lab3/        ← IA sobre los datos curados
```

---

## Tres niveles, y por qué están al mismo nivel de directorio

Es una pregunta razonable, porque los tres niveles son distintos:

| Nivel | Módulo | Qué es |
|-------|--------|--------|
| **Meta** | `bootstrap/` | Infraestructura **para Terraform**, no para la aplicación |
| **Core** | `payments/` | El sistema transaccional. El origen del dato |
| **Soluciones** | `lab1/`, `lab2/`, `lab3/` | Lo que se construye **sobre** el core |

### ¿Por qué `bootstrap/` no va dentro de `payments/`?

Porque su `backend.hcl` lo consumen **los cuatro módulos**, el helper
`scripts/lib/tf.sh` y dos workflows. Si viviera dentro de `payments/`, entonces
`lab1`, `lab2` y `lab3` dependerían de payments **solo para tener dónde guardar su
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
payments  ◀── EL CORE: Aurora Serverless, Fargate, API Gateway
    │
    ▼
  lab1    ── DMS desde Aurora ──▶ S3 raw/
    │
    ▼
  lab2    ── Glue: raw/ ──▶ curated/ + catálogo

  lab3    ── independiente: su propio Aurora con pgvector para el RAG
```

| Módulo | Lee el state de | Por qué |
|--------|-----------------|---------|
| `payments` | — | Es el core: no depende de nada |
| `lab1` | **`payments`** | VPC, subnets, endpoint de Aurora y su secreto |
| `lab2` | `lab1` | El bucket del data lake |
| `lab3` | — | Tiene su propia infraestructura para el vector store |

> `lab3` es independiente a propósito: si el core falla en la charla, el Lab 3
> arranca igual. Ver ADR-016.

---

## Orden de despliegue

```bash
export TF_VAR_db_password='CambiaEstaPassword2026!'

# 1. Una vez por cuenta AWS
cd infra/bootstrap && terraform init && terraform apply

# 2. El core (~10 min, casi todo Aurora)
cd ../payments
terraform init -backend-config=../backend.hcl && terraform apply

# 3. El schema del core, sobre Aurora
#    (subnets privadas: hace falta túnel SSM o ejecutarlo dentro de la VPC)
for f in ../../payments/schema/00*.sql; do psql "$CONN" -f "$f"; done

# 4. Los labs
cd ../lab1 && terraform init -backend-config=../backend.hcl && terraform apply
cd ../lab2 && terraform init -backend-config=../backend.hcl && terraform apply
cd ../lab3 && terraform init -backend-config=../backend.hcl && terraform apply
```

Los scripts de cada lab (`labs/*/scripts/setup.sh`) hacen los pasos 4 y siguientes,
y comprueban que el core exista antes de empezar.

---

## Orden de destrucción: inverso, y con un matiz

```bash
cd infra/lab2 && terraform destroy     # antes que lab1: escribe en su bucket
cd ../lab1    && terraform destroy     # antes que payments: sus ENIs de DMS
                                       # bloquearían el borrado de las subnets
cd ../lab3    && terraform destroy     # independiente, en cualquier momento
cd ../payments && terraform destroy
```

`bootstrap` **no se destruye**: el bucket tiene `prevent_destroy` porque contiene
el state de todo lo demás. Cuesta céntimos al mes.

---

## Por qué el state va remoto

El `terraform.tfstate` describe toda la infraestructura **y guarda las contraseñas
de Aurora en claro**. Nunca se commitea (está en `.gitignore`), y vive en S3 por dos
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
| `payments` | **~$0.11/hora** | Aurora (0.5 ACU) y el ALB no bajan a cero |
| `lab1` | **~$0.068/hora** | La instancia de DMS. No escala a cero |
| `lab2` | **$0** | Glue solo cobra cuando corre |
| `lab3` | **~$0.12/hora** | Aurora, Redis y EC2 |

Detalle en [COST_ESTIMATE.md](../COST_ESTIMATE.md).
