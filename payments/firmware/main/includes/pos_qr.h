#pragma once

#include "core2forAWS.h"

/*
 * Dibuja un QR con `text` dentro de `parent`, centrado.
 *
 * Devuelve el objeto creado, o NULL si el texto no cabe en un QR que se pueda
 * escanear desde el publico. El llamante no libera nada: LVGL se encarga al
 * limpiar la pantalla.
 */
lv_obj_t *pos_qr_draw(lv_obj_t *parent, const char *text, int max_px);

/*
 * Libera el buffer del ultimo QR dibujado.
 *
 * Hay que llamarlo al limpiar la pantalla: LVGL borra el objeto canvas pero no
 * el buffer que se le paso, y sin esto cada QR deja ~54 KB de PSRAM sin
 * recuperar.
 */
void pos_qr_free(void);
