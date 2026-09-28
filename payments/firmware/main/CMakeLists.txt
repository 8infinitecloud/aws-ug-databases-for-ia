set(COMPONENT_SRCS
    "main.c"
    "wifi.c"
    "pos_api.c"
    "pos_qr.c"
    "pos_ui.c")
set(COMPONENT_ADD_INCLUDEDIRS "./includes")
set(COMPONENT_REQUIRES
    "nvs_flash"
    "esp_http_client"
    "json"
    "core2forAWS"
    "qrcodegen")

register_component()

# La CA raiz de Amazon va empotrada en el binario: sin sistema de ficheros, es
# la unica forma de validar el certificado de API Gateway.
target_add_binary_data(${COMPONENT_TARGET} "certs/aws-root-ca.pem" TEXT)
