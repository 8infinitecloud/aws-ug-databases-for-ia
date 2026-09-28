#!/usr/bin/env bash
#
# Trae los componentes del AWS IoT Kit, que no viven en este repositorio.
#
# Son 20 MB de codigo de M5Stack y AWS (el driver de la pantalla, el elemento
# seguro ATECC608 y el cliente de IoT Core). Versionarlos aqui duplicaria codigo
# de terceros y engordaria el clon del taller sin aportar nada: son los mismos
# ficheros para todo el mundo.
#
#   ./setup.sh            descarga los componentes
#   ./setup.sh --local    los copia de un clon que ya tengas (mas rapido)
#
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="https://github.com/m5stack/Core2-for-AWS-IoT-EduKit.git"

# Clavado a un commit y no a una rama: una demo tiene que compilar igual el dia
# de la charla que hoy.
COMMIT="3ec44e0"
PROYECTO="Blinky-Hello-World"

COMPONENTES=(core2forAWS esp-aws-iot esp-cryptoauthlib)

# El generador de QR va aparte: no esta en Blinky-Hello-World sino en
# Getting-Started, y de ese componente solo sirve la libreria de Nayuki
# (qrcodegen). El envoltorio `qrcode.c` que lo acompana hace printf del QR a
# la consola serie, que no es lo que necesita un POS: aqui el QR se dibuja en
# la pantalla, y eso se hace con getSize()/getModule().
QR_ORIGEN="Getting-Started/components/qrcode/src"

rojo=$'\033[0;31m'; verde=$'\033[0;32m'; azul=$'\033[0;34m'; tenue=$'\033[2m'; nc=$'\033[0m'

copiar_desde() {
    local origen="$1"
    for c in "${COMPONENTES[@]}"; do
        if [[ ! -d "$origen/components/$c" ]]; then
            echo "${rojo}[✗]${nc} falta $c en $origen" >&2
            return 1
        fi
        rm -rf "${AQUI:?}/components/$c"
        cp -R "$origen/components/$c" "$AQUI/components/$c"
        echo "  ${verde}✓${nc} $c"
    done

    mkdir -p "$AQUI/main/certs"
    cp "$origen/main/certs/aws-root-ca.pem" "$AQUI/main/certs/aws-root-ca.pem"
    echo "  ${verde}✓${nc} aws-root-ca.pem"
}

copiar_qrcodegen() {
    local raiz="$1"
    local src="$raiz/$QR_ORIGEN"

    if [[ ! -f "$src/qrcodegen.c" ]]; then
        echo "${rojo}[✗]${nc} falta qrcodegen.c en $src" >&2
        return 1
    fi

    mkdir -p "$AQUI/components/qrcodegen/include"
    cp "$src/qrcodegen.c" "$AQUI/components/qrcodegen/qrcodegen.c"
    cp "$src/qrcodegen.h" "$AQUI/components/qrcodegen/include/qrcodegen.h"

    cat > "$AQUI/components/qrcodegen/CMakeLists.txt" <<'CM'
# Libreria de generacion de QR (Nayuki, MIT). Se registra como componente propio
# porque en el kit viene dentro de `qrcode`, junto a un envoltorio que imprime el
# QR por la consola serie y que aqui no sirve.
idf_component_register(SRCS "qrcodegen.c"
                       INCLUDE_DIRS "include")
CM
    echo "  ${verde}✓${nc} qrcodegen ${tenue}(MIT, de Getting-Started)${nc}"
}

mkdir -p "$AQUI/components"

if [[ "${1:-}" == "--local" ]]; then
    ORIGEN="${2:-}"
    if [[ -z "$ORIGEN" ]]; then
        # Sitio por defecto: al lado de este repositorio.
        ORIGEN="$(cd "$AQUI/../../.." && pwd)/Core2-for-AWS-IoT-EduKit/$PROYECTO"
    fi
    echo "${azul}Copiando desde${nc} $ORIGEN"
    copiar_desde "$ORIGEN"
    copiar_qrcodegen "$(cd "$ORIGEN/.." && pwd)"
else
    TMP="$(mktemp -d)"
    # shellcheck disable=SC2064  # se quiere expandir TMP ahora, no al salir
    trap "rm -rf '$TMP'" EXIT

    echo "${azul}Descargando los componentes del kit${nc} ${tenue}(~20 MB, commit $COMMIT)${nc}"
    git clone --quiet --filter=blob:none --no-checkout "$REPO" "$TMP/kit"
    git -C "$TMP/kit" sparse-checkout set --no-cone \
        "/$PROYECTO/components" "/$PROYECTO/main/certs" "/$QR_ORIGEN"
    git -C "$TMP/kit" checkout --quiet "$COMMIT"
    copiar_desde "$TMP/kit/$PROYECTO"
    copiar_qrcodegen "$TMP/kit"
fi

echo
echo "${verde}Listo.${nc} Ahora:"
echo "  ${tenue}pio run${nc}                      compilar"
echo "  ${tenue}pio run -t upload -t monitor${nc}  grabar y ver el log"
