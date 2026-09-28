#pragma once

#include "esp_err.h"

/* Conecta a la red configurada y bloquea hasta tener IP o agotar reintentos. */
esp_err_t wifi_connect(void);
bool      wifi_is_connected(void);
