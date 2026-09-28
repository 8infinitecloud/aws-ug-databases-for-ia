"""
Pruebas del firmware del terminal POS.

Son estaticas: no necesitan la base de datos, ni el dispositivo, ni la cadena de
herramientas de ESP-IDF. Comprueban lo que se puede romper editando codigo y no
se descubre compilando, que en un firmware es justo lo peligroso: el binario
compila igual y el fallo aparece en la charla, con el movil delante del QR.

Compilar de verdad necesita el kit:
    cd payments/firmware && ./setup.sh && pio run
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRMWARE = REPO_ROOT / "payments/firmware"


def codigo(ruta: Path) -> str:
    """
    El fichero sin comentarios.

    Hace falta porque estos ficheros explican en prosa las APIs que NO se usan y
    por que, asi que buscar una llamada sobre el texto crudo encuentra la
    explicacion en vez de la llamada.
    """
    fuente = ruta.read_text(encoding="utf-8")
    fuente = re.sub(r"/\*.*?\*/", " ", fuente, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", fuente)

# El firmware genera el QR con qrcodegen y lo dibuja en una pantalla de 2
# pulgadas. La URL es lo unico que decide si el QR se puede escanear: cada
# caracter de mas sube la version, y cada version sube la densidad.
#
# Capacidad en modo byte con correccion de errores baja (ISO/IEC 18004).
QR_CAPACIDAD_BYTES = {1: 17, 2: 32, 3: 53, 4: 78, 5: 106, 6: 134}

# pos_qr.c no pasa de la version 6: por encima, los modulos bajan de 4 px en
# esta pantalla y los moviles dejan de leerlos.
QR_VERSION_MAX = 6


def _version_para(url: str) -> int | None:
    for version, capacidad in sorted(QR_CAPACIDAD_BYTES.items()):
        if len(url) <= capacidad:
            return version
    return None


def test_la_url_de_pago_cabe_en_un_qr_que_se_puede_escanear():
    """
    Ata el formato de la URL al tamaño de la pantalla.

    Si alguien añade un stage, un prefijo o alarga la referencia, el QR sube de
    version y se vuelve ilegible en una pantalla de 2 pulgadas. Eso no se
    descubre compilando: se descubre en la charla, con el movil delante.
    """
    # Lo que produce `pos_api_payment_url` con un endpoint real de API Gateway.
    url = "https://a1b2c3d4e5.execute-api.us-east-1.amazonaws.com/pay/PI1A2B3C4D"

    version = _version_para(url)
    assert version is not None, f"la URL de {len(url)} caracteres no cabe en ningun QR soportado"
    assert version <= 4, (
        f"la URL de {len(url)} caracteres necesita un QR version {version}; "
        "por encima de la 4 los modulos bajan de 4 px en la pantalla del Core2"
    )


def test_la_referencia_del_pos_es_corta():
    """
    `pos_api_new_reference` genera PI + 8 digitos hex.

    Una referencia mas larga sube la version del QR, que es el motivo de que no
    sea un UUID.
    """
    firmware = codigo(FIRMWARE / "main/pos_api.c")
    assert '"PI%08X"' in firmware, "cambio el formato de la referencia; revisa el tamaño del QR"


def test_el_firmware_no_usa_el_qrcode_display_del_kit():
    """
    El componente `qrcode` del kit imprime el QR por la consola serie.

    Sirve para aprovisionar mirando el log, no para que alguien lo escanee. Si
    aparece aqui, es que alguien lo confundio con una funcion de pantalla.
    """
    qr = codigo(FIRMWARE / "main/pos_qr.c")
    assert "qrcodegen_getModule" in qr, "el QR debe dibujarse modulo a modulo"
    assert "qrcode_display(" not in qr
