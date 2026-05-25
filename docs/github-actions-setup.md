# Setup de GitHub Actions con OIDC

Los workflows usan **OpenID Connect (OIDC)** para autenticarse en AWS sin guardar Access Keys como secrets. GitHub obtiene un token firmado de AWS STS directamente.

## Por qué OIDC y no Access Keys

| | Access Keys | OIDC |
|--|------------|------|
| Rotación | Manual, fácil de olvidar | Automática por STS |
| Scope | Global para el usuario IAM | Token de 1 hora, solo para ese workflow run |
| Riesgo si se filtran | Alto — acceso permanente | Bajo — expiran en 1 hora |
| Setup | Copiar keys a GitHub Secrets | Un OIDC Provider + un IAM Role |

## Pasos de configuración

### 1. Crear el OIDC Provider en AWS (una sola vez por cuenta)

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

Verificar que existe:
```bash
aws iam list-open-id-connect-providers
```

### 2. Crear el IAM Role que GitHub va a asumir

Reemplaza `TU_ORG_O_USER` y `TU_REPO` con los valores reales:

```bash
GITHUB_ORG="8infinitecloud"      # tu usuario u organización de GitHub
GITHUB_REPO="aws-ug-databases-for-ia"
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION="us-east-1"

# Trust policy: solo este repo puede asumir el rol, y solo desde la rama main
cat > /tmp/trust-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::${AWS_ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:${GITHUB_ORG}/${GITHUB_REPO}:*"
        }
      }
    }
  ]
}
EOF

# Crear el rol
aws iam create-role \
  --role-name rag-lab-github-actions-role \
  --assume-role-policy-document file:///tmp/trust-policy.json \
  --description "Role para GitHub Actions — RAG Lab"

# Adjuntar permisos necesarios para el lab
aws iam attach-role-policy \
  --role-name rag-lab-github-actions-role \
  --policy-arn arn:aws:iam::aws:policies/AmazonBedrockFullAccess

aws iam attach-role-policy \
  --role-name rag-lab-github-actions-role \
  --policy-arn arn:aws:iam::aws:policies/AmazonDynamoDBFullAccess

aws iam attach-role-policy \
  --role-name rag-lab-github-actions-role \
  --policy-arn arn:aws:iam::aws:policies/AmazonS3FullAccess

aws iam attach-role-policy \
  --role-name rag-lab-github-actions-role \
  --policy-arn arn:aws:iam::aws:policies/AmazonSSMFullAccess

aws iam attach-role-policy \
  --role-name rag-lab-github-actions-role \
  --policy-arn arn:aws:iam::aws:policies/AWSCloudFormationFullAccess

# Permisos para crear VPC, RDS, ElastiCache, IAM
aws iam attach-role-policy \
  --role-name rag-lab-github-actions-role \
  --policy-arn arn:aws:iam::aws:policies/PowerUserAccess

echo "ARN del rol:"
aws iam get-role \
  --role-name rag-lab-github-actions-role \
  --query "Role.Arn" --output text
```

> **Nota de seguridad:** `PowerUserAccess` es amplio, apropiado para el lab. En producción, crear una policy custom con solo los permisos necesarios.

### 3. Configurar GitHub Secrets

En tu repositorio: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Valor |
|--------|-------|
| `AWS_DEPLOY_ROLE_ARN` | ARN del rol creado arriba (`arn:aws:iam::ACCOUNT:role/rag-lab-github-actions-role`) |
| `AWS_REGION` | `us-east-1` (o tu región) |

### 4. Crear el GitHub Environment "lab"

En tu repositorio: **Settings → Environments → New environment**

- Nombre: `lab`
- Protection rules: opcional (puedes requerir aprobación manual antes de deploy)

### 5. Verificar que funciona

Haz un push pequeño a `main` (edita un comentario en `app/app.py`) y verifica que el workflow **CI — Validate** pasa. Luego, desde **Actions → Deploy App → Run workflow**, ejecuta manualmente el deploy.

## Flujo completo de workflows

```
PR abierto
    └─► CI — Validate (automático)
            ├─ Python syntax
            ├─ Shell (shellcheck)
            ├─ CloudFormation (cfn-lint)
            └─ Corpus documents

Push a main (cambios en app/ o data/)
    └─► Deploy App (automático)
            ├─ Package código → S3
            ├─ SSM Run Command en EC2
            └─ Streamlit reinicia

Manual (primera vez o cambio de infra)
    └─► Deploy Infrastructure
            ├─ cfn validate
            ├─ CloudFormation deploy
            └─► dispara Deploy App automáticamente
```
