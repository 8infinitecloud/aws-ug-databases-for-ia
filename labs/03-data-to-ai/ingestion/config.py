"""
Configuración centralizada del laboratorio.

Por qué un módulo de config en vez de leer os.getenv() en cada archivo:
- Punto único de fallo visible: si falta una variable, el error ocurre aquí al importar
- Facilita testing: basta con mockear este módulo
- La audiencia del lab ve todos los parámetros del sistema en un solo lugar
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class BedrockConfig:
    region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    embedding_model: str = field(
        default_factory=lambda: os.getenv(
            "BEDROCK_EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0"
        )
    )
    llm_model: str = field(
        default_factory=lambda: os.getenv(
            "BEDROCK_LLM_MODEL", "amazon.nova-pro-v1:0"
        )
    )
    # Titan V2 soporta 256, 512 o 1024. En el lab usamos 1024 para máxima precisión;
    # en producción bajar a 256 reduce costo de storage ~75% con pérdida mínima de calidad.
    embedding_dimensions: int = field(
        default_factory=lambda: int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))
    )


@dataclass(frozen=True)
class AuroraConfig:
    host: str = field(default_factory=lambda: os.getenv("AURORA_HOST", "localhost"))
    port: int = field(default_factory=lambda: int(os.getenv("AURORA_PORT", "5432")))
    database: str = field(default_factory=lambda: os.getenv("AURORA_DB", "compliance_db"))
    user: str = field(default_factory=lambda: os.getenv("AURORA_USER", "ragadmin"))
    # Contraseña nunca en env plain — se obtiene de Secrets Manager en runtime
    secret_arn: str = field(
        default_factory=lambda: os.getenv("AURORA_SECRET_ARN", "")
    )
    # Para desarrollo local sin Secrets Manager, se puede usar AURORA_PASSWORD
    password_override: str = field(
        default_factory=lambda: os.getenv("AURORA_PASSWORD", "")
    )
    table_name: str = "document_chunks"


@dataclass(frozen=True)
class RedisConfig:
    host: str = field(default_factory=lambda: os.getenv("REDIS_HOST", "localhost"))
    port: int = field(default_factory=lambda: int(os.getenv("REDIS_PORT", "6379")))
    # TTL corto: la memoria de sesión es efímera por diseño. 2h para el lab; en producción 8h.
    session_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("REDIS_SESSION_TTL", "7200"))
    )
    max_session_messages: int = field(
        default_factory=lambda: int(os.getenv("MAX_SESSION_MESSAGES", "10"))
    )


@dataclass(frozen=True)
class DynamoDBConfig:
    table_name: str = field(
        default_factory=lambda: os.getenv("DYNAMODB_TABLE", "rag_user_memory")
    )
    region: str = field(default_factory=lambda: os.getenv("DYNAMODB_REGION", "us-east-1"))


@dataclass(frozen=True)
class S3Config:
    bucket: str = field(default_factory=lambda: os.getenv("S3_BUCKET", ""))
    prefix: str = field(default_factory=lambda: os.getenv("S3_PREFIX", "compliance-docs/"))


@dataclass(frozen=True)
class ChunkingConfig:
    chunk_size: int = field(default_factory=lambda: int(os.getenv("CHUNK_SIZE", "1000")))
    chunk_overlap: int = field(
        default_factory=lambda: int(os.getenv("CHUNK_OVERLAP", "200"))
    )
    # Separadores en orden de preferencia — párrafos > líneas > frases > palabras.
    # Mantener párrafos completos es crítico para documentos de compliance donde
    # cortar en medio de una definición legal pierde el contexto.
    separators: list = field(
        default_factory=lambda: ["\n\n", "\n", ". ", " ", ""]
    )


@dataclass(frozen=True)
class RetrievalConfig:
    top_k: int = field(default_factory=lambda: int(os.getenv("RETRIEVAL_TOP_K", "5")))
    # Score mínimo de similitud coseno. 0.70 elimina chunks no relacionados
    # sin ser tan estricto que devuelva resultados vacíos.
    min_score: float = field(
        default_factory=lambda: float(os.getenv("RETRIEVAL_MIN_SCORE", "0.70"))
    )


@dataclass(frozen=True)
class LabConfig:
    demo_user_id: str = field(
        default_factory=lambda: os.getenv("DEMO_USER_ID", "demo-user-001")
    )
    verbose: bool = field(
        default_factory=lambda: os.getenv("VERBOSE_LOGGING", "true").lower() == "true"
    )


# Instancias singleton — importar desde aquí, no instanciar en cada módulo
bedrock = BedrockConfig()
aurora = AuroraConfig()
redis_cfg = RedisConfig()
dynamodb = DynamoDBConfig()
s3 = S3Config()
chunking = ChunkingConfig()
retrieval = RetrievalConfig()
lab = LabConfig()
