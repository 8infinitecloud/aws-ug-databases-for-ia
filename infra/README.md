# Infraestructura — Terraform

Toda la infraestructura de los tres labs. Un módulo raíz por lab, con state
independiente: si el Lab 1 se rompe, el Lab 3 sigue desplegable.

```
infra/
├── bootstrap/        crea el backend remoto (se corre UNA vez)
├── lab1/             VPC, RDS MySQL, DMS, S3 data lake
├── lab2/             Glue Job, Crawler, Catalog, DQ Ruleset
└── lab3/             Aurora pgvector, Redis, DynamoDB, S3, EC2
```

---

## Por qué el state va remoto

El `terraform.tfstate` describe toda la infraestructura del demo, **y guarda las
contraseñas de RDS y Aurora en claro**. Nunca se commitea (está en
`.gitignore`), y vive en S3 por dos razones:

1. La demo se despliega desde dos sitios — tu laptop y GitHub Actions. Con state
   local, el workflow arrancaría de cero e intentaría recrear lo que ya existe.
2. La tabla DynamoDB de locks evita que dos `apply` simultáneos lo corrompan.

---

## Primera vez: bootstrap

Es el problema del huevo y la gallina — este módulo crea el bucket donde vivirá
el state de todo lo demás, así que su propio state es local.

```bash
cd infra/bootstrap
terraform init
terraform apply
```

Crea el bucket `fincorp-datalake-tfstate-<account-id>`, la tabla de locks, y
escribe `infra/backend.hcl`, que es lo que consumen los tres labs.

> Solo se ejecuta una vez por cuenta AWS. El bucket tiene `prevent_destroy`:
> un `terraform destroy` aquí sería irreversible para los otros tres módulos.

---

## Desplegar un lab

Los tres siguen el mismo patrón. La contraseña va por variable de entorno, nunca
en un fichero:

```bash
export TF_VAR_db_password="FinCorp2026!"

cd infra/lab1
terraform init -backend-config=../backend.hcl
terraform plan
terraform apply
```

El `-backend-config` es obligatorio la primera vez en cada módulo: el bloque
`backend` de Terraform no admite interpolación, así que el nombre del bucket
(que lleva el account id) no puede vivir dentro de `backend.tf`.

### Orden y dependencias

| Módulo | Depende de | Tiempo | Notas |
|--------|-----------|--------|-------|
| `bootstrap` | — | ~30 s | Una sola vez por cuenta |
| `lab1` | bootstrap | ~15 min | La instancia DMS es lo que tarda |
| `lab2` | **lab1** | ~1 min | Lee el state del Lab 1 |
| `lab3` | bootstrap | ~20 min | Independiente de los Labs 1 y 2 |

El Lab 2 lee los outputs del Lab 1 con `terraform_remote_state`. En
CloudFormation había que copiar el nombre del bucket a mano entre stacks; aquí
si el Lab 1 cambia de bucket, el Lab 2 se entera solo.

Los Labs 1 y 3 son independientes: se pueden lanzar en paralelo, que es
justo lo que hace el runbook para no esperar 35 minutos en serie.

---

## Variables

Todas tienen default salvo las contraseñas.

| Variable | Módulo | Default |
|----------|--------|---------|
| `db_password` | lab1, lab3 | **sin default** — `export TF_VAR_db_password=...` |
| `aws_region` | todos | `us-east-1` |
| `project_name` | bootstrap, lab1, lab2 | `fincorp-datalake` |
| `project_name` | lab3 | `rag-lab` |
| `environment` | lab1, lab2 | `lab` |
| `mysql_version` | lab1 | `8.0.43` |
| `aurora_engine_version` | lab3 | `16.10` |

> Las versiones de motor están fijadas a propósito. AWS deprecia versiones y
> deja de permitir crear instancias nuevas con ellas — es exactamente lo que
> pasaba con Aurora 16.4 y con el `"8.0"` genérico de MySQL.

---

## Destruir

En orden inverso, y el Lab 2 antes que el Lab 1: el Lab 2 escribe en el bucket
que crea el Lab 1.

```bash
cd infra/lab2 && terraform destroy
cd ../lab1 && terraform destroy
cd ../lab3 && terraform destroy
```

Los buckets de datos llevan `force_destroy = true`, así que `destroy` se lleva
también los objetos versionados. El bucket del **state** no: ese sobrevive a
propósito.

---

## Consultar outputs

Los scripts de cada lab los leen de aquí, así que están disponibles igual:

```bash
cd infra/lab1
terraform output                      # todos
terraform output -raw s3_bucket_name  # uno solo, sin comillas
```
