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
