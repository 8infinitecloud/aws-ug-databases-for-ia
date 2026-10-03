# Preparación — antes de empezar

> Tiempo: ~15 minutos (más la espera de habilitar Bedrock, que puede tardar).
> Nada de esto cuesta dinero.

Cuando acabes esta página, ejecuta `./scripts/preflight.sh`. Si sale en verde,
estás listo para el Lab 1.

---

## 1. Lo que necesitas

| Requisito | Por qué | Labs |
|-----------|---------|------|
| Cuenta AWS con permisos de administrador | El workshop crea VPC, EC2, DMS, Glue, Aurora, IoT, IAM | todos |
| AWS CLI v2 | Los scripts la usan para consultar estado | 1, 2, 3 |
| Terraform ≥ 1.6 | Toda la infraestructura | 1, 2, 3 |
| `jq` | Los scripts parsean JSON | 1, 2 |
| Python 3.11+ | Pipeline de ingesta y Streamlit | 3 |
| Docker | Construir la imagen del backend del core (`deploy_core.sh`) y, en local, los tests contra PostgreSQL | core |
| Acceso a modelos de Bedrock | Embeddings y LLM | 3 |

**Región:** todo el workshop asume `us-east-1`. Si usas otra, exporta
`AWS_REGION` y compruébalo en cada paso: no todos los servicios están en todas
las regiones y Bedrock es especialmente desigual.

---

## 2. Instalar las herramientas

### macOS

```bash
brew install awscli terraform jq python@3.11
brew install --cask docker
```

### Ubuntu / Debian

```bash
# AWS CLI v2 (el paquete de apt suele ser v1: no sirve)
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip awscliv2.zip && sudo ./aws/install

# Terraform
wget -O- https://apt.releases.hashicorp.com/gpg | \
  sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] \
  https://apt.releases.hashicorp.com $(lsb_release -cs) main" | \
  sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install terraform

sudo apt install -y jq python3.11 python3.11-venv docker.io
```

### Windows

Usa **WSL2** con Ubuntu y sigue las instrucciones de Ubuntu. Los scripts del
workshop son bash y asumen utilidades de Unix; no se han probado en PowerShell.

---

## 3. Configurar las credenciales

```bash
aws configure
```

```
AWS Access Key ID     [None]: AKIA...
AWS Secret Access Key [None]: ...
Default region name   [None]: us-east-1
Default output format [None]: json
```

Comprueba:

```bash
aws sts get-caller-identity
```

Debe devolver tu `Account` y tu `Arn`. Si da error, las credenciales no están
bien y nada del workshop va a funcionar.

### Permisos IAM

El camino corto es un usuario con `AdministratorAccess`. Si necesitas algo más
acotado, el workshop toca estos servicios:

```
rds:*            dms:*              s3:*
glue:*           secretsmanager:*   iam:*
ec2:*            elasticache:*      dynamodb:*
bedrock:InvokeModel              bedrock:ListFoundationModels
ssm:*            logs:*            sts:GetCallerIdentity
```

> `iam:*` y `ec2:*` no son opcionales: Terraform crea roles para DMS, Glue y el
> EC2 de la app, y una VPC completa por lab.

---

## 4. Habilitar los modelos de Bedrock

**Este paso es el que más gente olvida, y el Lab 3 no arranca sin él.**

1. Abre la [consola de Bedrock](https://console.aws.amazon.com/bedrock/) en `us-east-1`
2. Menú lateral → **Model access**
3. **Enable specific models** (o *Manage model access*)
4. Marca:
   - **Amazon Titan Text Embeddings V2** (`amazon.titan-embed-text-v2:0`)
   - **Amazon Nova Pro** (`amazon.nova-pro-v1:0`)
5. Guarda y espera. Suele ser inmediato, pero puede tardar unos minutos.

Comprueba desde la terminal:

```bash
aws bedrock list-foundation-models --region us-east-1 \
  --query "modelSummaries[?contains(modelId,'titan-embed-text-v2') || contains(modelId,'nova-pro')].modelId" \
  --output table
```

> **Por qué Nova Pro y no Claude:** es más barato para un lab que se repite
> muchas veces. Puedes cambiarlo en `labs/03-data-to-ai/.env` con
> `BEDROCK_LLM_MODEL`, pero habilita antes el modelo que elijas.

---

## 5. Clonar y comprobar

```bash
git clone <url-del-repo>
cd aws-ug-databases-for-ia

./scripts/preflight.sh
```

Esperado al final:

```
Resumen
  Todo listo. Puedes empezar por el Lab 1.
```

Las advertencias no bloquean. Los errores sí: el script dice qué hacer en cada
caso.

---

## 6. Crear el backend de Terraform

Se ejecuta **una sola vez por cuenta AWS**. Crea el bucket S3 donde vivirá el
estado de Terraform y la tabla DynamoDB de locks.

```bash
cd infra/bootstrap
terraform init
terraform apply
```

Escribe `yes` cuando lo pida. Tarda ~30 segundos y crea:

- `fincorp-datalake-tfstate-<tu-account-id>` — bucket del estado
- `fincorp-datalake-tflock` — tabla de locks
- `infra/backend.hcl` — configuración que consumen los tres labs

> **¿Por qué no estado local?** El estado describe toda tu infraestructura y
> guarda las contraseñas en claro. En S3 sobrevive a que borres la carpeta, y el
> lock evita que dos `apply` simultáneos lo corrompan. `infra/backend.hcl` y el
> estado están en `.gitignore`: nunca se commitean.

---

## 7. Entornos de Python (hay dos, a propósito)

| Entorno | Para qué | Cuándo |
|---------|----------|--------|
| `requirements.txt` (raíz) | `scripts/validate_setup.py` y ejecutar `glue_job.py` en local | Opcional, útil desde el Lab 1 |
| `labs/03-data-to-ai/requirements.txt` | La app RAG: LangChain, Streamlit, pgvector | Obligatorio en el Lab 3 |

No se pisan: el de la raíz lleva pandas/pyarrow y el del Lab 3 lleva LangChain y
Streamlit. Si quieres los dos, usa dos virtualenvs.

```bash
# Herramientas del workshop (raíz)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

El del Lab 3 se instala cuando llegues a ese lab; su README lo indica.

---

## 8. Fijar la contraseña de las bases de datos

Los Labs 1 y 3 crean bases de datos. La contraseña va por variable de entorno,
nunca en un fichero:

```bash
export TF_VAR_db_password='CambiaEstaPassword2026!'
```

Mínimo 8 caracteres. Tenla a mano en la misma terminal durante todo el workshop
— si abres otra, vuelve a exportarla.

---

## Ya está

```bash
./scripts/preflight.sh    # todo en verde, backend incluido
```

**Siguiente:** [Lab 1 — Ingestion + Data Lake](../labs/01-ingestion-data-lake/README.md)

¿Algo falla? → [docs/troubleshooting.md](troubleshooting.md)
