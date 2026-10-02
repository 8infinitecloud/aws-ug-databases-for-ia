"""Configuracion del servicio, toda por variables de entorno."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    # ── Base de datos ─────────────────────────────────────────────────────────
    db_host: str = field(default_factory=lambda: os.getenv("DB_HOST", "localhost"))
    db_port: int = field(default_factory=lambda: int(os.getenv("DB_PORT", "5432")))
    db_name: str = field(default_factory=lambda: os.getenv("DB_NAME", "acquiring"))
    db_user: str = field(default_factory=lambda: os.getenv("DB_USER", "postgres"))
    # En Fargate la contrasena llega de Secrets Manager por secret_arn. DB_PASSWORD
    # solo existe para desarrollo local.
    db_password: str = field(default_factory=lambda: os.getenv("DB_PASSWORD", ""))
    db_secret_arn: str = field(default_factory=lambda: os.getenv("DB_SECRET_ARN", ""))

    # ── AWS ───────────────────────────────────────────────────────────────────
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    # Si esta vacio, los eventos se registran en el log y no se publican. Permite
    # desarrollar sin IoT Core.
    iot_endpoint: str = field(default_factory=lambda: os.getenv("IOT_ENDPOINT", ""))
    # El topic de los pagos sin dispositivo (online): <prefijo>/events. Los del
    # POS cuelgan del serial de su terminal. Los dos casan con el filtro
    # `+/events` de la regla del Lab 4.
    iot_topic_prefix: str = field(
        default_factory=lambda: os.getenv("IOT_TOPIC_PREFIX", "online")
    )

    # La URL con la que el comprador llega a esta API (API Gateway). Hace falta
    # para la redireccion de 3DS: detras del balanceador, el host de la peticion
    # no es el publico. Vacia en local.
    public_base_url: str = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL", ""))

    # ── Mock de autorizacion ──────────────────────────────────────────────────
    # DEMO: el CVV que aprueba. Va por entorno para poder cambiarlo desde la
    # consola antes de la charla, sin reflashear el POS ni redesplegar nada.
    demo_cvv: str = field(default_factory=lambda: os.getenv("DEMO_CVV", "123"))
    demo_card_token: str = field(
        default_factory=lambda: os.getenv("DEMO_CARD_TOKEN", "demo_card_4242")
    )

    @property
    def db_conninfo(self) -> str:
        password = self.db_password or _password_from_secrets_manager(
            self.db_secret_arn, self.aws_region
        )
        return (
            f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
            f"user={self.db_user} password={password}"
        )


def _password_from_secrets_manager(secret_arn: str, region: str) -> str:
    """
    Lee la contrasena de Secrets Manager.

    Se importa boto3 aqui y no arriba para que el servicio arranque en local sin
    credenciales AWS: si no hay secret_arn, nunca se llega a esta linea.
    """
    if not secret_arn:
        return ""
    import boto3

    client = boto3.client("secretsmanager", region_name=region)
    payload = client.get_secret_value(SecretId=secret_arn)["SecretString"]
    return json.loads(payload)["password"]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
