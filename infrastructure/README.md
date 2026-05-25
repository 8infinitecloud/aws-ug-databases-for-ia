# Infraestructura AWS

Stack de CloudFormation que provisiona todos los recursos necesarios para el lab.

## Recursos Creados

| Recurso | Tipo | Propósito |
|---------|------|-----------|
| VPC | `AWS::EC2::VPC` | Red aislada para los recursos del lab |
| 2 Subnets privadas | `AWS::EC2::Subnet` | Requerimiento de Aurora Multi-AZ |
| 1 Subnet pública | `AWS::EC2::Subnet` | Acceso a Internet para el setup |
| Aurora PostgreSQL Serverless v2 | `AWS::RDS::DBCluster` | Vector store (pgvector) + memoria semántica |
| ElastiCache Redis | `AWS::ElastiCache::CacheCluster` | Memoria de sesión (Capa 1) |
| DynamoDB Table | `AWS::DynamoDB::Table` | Memoria persistente de usuario (Capa 2) |
| S3 Bucket | `AWS::S3::Bucket` | Almacenamiento de documentos fuente |
| Secrets Manager | `AWS::SecretsManager::Secret` | Credenciales de Aurora |
| IAM Role | `AWS::IAM::Role` | Permisos de la app (Bedrock, DynamoDB, S3) |

## Parámetros

| Parámetro | Default | Descripción |
|-----------|---------|-------------|
| `ProjectName` | `rag-lab` | Prefijo para nombre de recursos |
| `DBPassword` | `RagLab2024!` | Password de Aurora — cambiar en producción |
| `EnvironmentType` | `lab` | Afecta el tamaño máximo de Aurora (lab=4 ACU, prod=16 ACU) |

## Scripts

| Script | Qué hace | Cuándo ejecutar |
|--------|---------|----------------|
| `setup.sh` | Provisiona **solo infraestructura** (CloudFormation) | Una vez, 20-25 min antes del demo |
| `deploy-app.sh` | Empaqueta y despliega **solo la app** en el EC2 vía SSM | Después de `setup.sh`, y cada vez que cambies código |
| `cleanup.sh` | Elimina todos los recursos AWS | Al terminar el lab |

Los scripts están separados porque tienen propósitos y tiempos distintos:
- Infra: 20 min, rara vez cambia
- App: 3-5 min, puede cambiar varias veces durante el desarrollo

## Deployment

```bash
# Paso 1: infraestructura (una vez, ~20 min)
chmod +x setup.sh && ./setup.sh

# Paso 2: aplicación (después de setup, repetible en ~5 min)
chmod +x deploy-app.sh && ./deploy-app.sh

# Re-desplegar app con re-ingesta de documentos
./deploy-app.sh --reingest
```

## Cleanup

```bash
chmod +x cleanup.sh && ./cleanup.sh
```

## Por qué Aurora Serverless v2 y no Aurora Provisioned

`MinCapacity: 0.5` permite que Aurora escale casi a cero cuando está idle. Para el lab esto es crucial: si el presentador hace una pausa de 15 minutos, la DB no está consumiendo 2 ACU en espera. El costo mínimo efectivo en lab es ~$0.06/hora.

## Tiempo de Provisioning

- VPC, DynamoDB, S3: ~2 minutos
- Aurora PostgreSQL Serverless v2: ~10-15 minutos
- ElastiCache Redis: ~3-5 minutos
- **Total: ~15-20 minutos**

Ejecutar `setup.sh` al menos 20 minutos antes del demo en vivo.
