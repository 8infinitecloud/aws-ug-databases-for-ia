/*
 * Dibuja un QR en la pantalla del Core2.
 *
 * El kit trae un componente `qrcode` con una funcion `qrcode_display()`, pero
 * esa hace printf del QR en la consola serie: sirve para aprovisionar un
 * dispositivo mirando el log, no para que alguien lo escanee desde el publico.
 * Aqui se usa la libreria que hay debajo (qrcodegen, de Nayuki) y se pinta el
 * mapa de modulos sobre un canvas de LVGL.
 */

#include <string.h>

#include "esp_log.h"
#include "esp_heap_caps.h"
#include "qrcodegen.h"

#include "pos_qr.h"

static const char *TAG = "pos_qr";

/*
 * Version 6 son 41x41 modulos. Por encima de eso, con una pantalla de 2
 * pulgadas, cada modulo baja de 4 px y los moviles dejan de leerlo a la
 * distancia a la que esta el publico.
 */
#define QR_MAX_VERSION 6

/* Zona tranquila: 4 modulos es lo que pide la norma. Con menos, muchos
 * lectores no encuentran el patron. */
#define QR_QUIET 4

static uint8_t *qr_buf;
static uint8_t *tmp_buf;

/*
 * El buffer del canvas del QR anterior.
 *
 * LVGL no libera los buffers externos cuando borra el objeto: `lv_obj_clean()`
 * se lleva el canvas y deja los bytes. Cada QR fugaba ~54 KB de PSRAM, y en una
 * demo con varias ventas eso acaba agotandola y el QR deja de dibujarse — sin
 * error visible, simplemente no aparece.
 */
static void *canvas_anterior;

lv_obj_t *pos_qr_draw(lv_obj_t *parent, const char *text, int max_px)
{
    if (qr_buf == NULL) {
        /* De PSRAM: son ~1 KB cada uno, pero la RAM interna del Core2 es la
         * que necesitan el WiFi y TLS, y conviene no disputarsela. */
        size_t n = qrcodegen_BUFFER_LEN_FOR_VERSION(QR_MAX_VERSION);
        qr_buf  = heap_caps_malloc(n, MALLOC_CAP_SPIRAM);
        tmp_buf = heap_caps_malloc(n, MALLOC_CAP_SPIRAM);
        if (qr_buf == NULL || tmp_buf == NULL) {
            ESP_LOGE(TAG, "sin memoria para los buffers del QR");
            return NULL;
        }
    }

    bool ok = qrcodegen_encodeText(text, tmp_buf, qr_buf,
                                   qrcodegen_Ecc_LOW,
                                   qrcodegen_VERSION_MIN, QR_MAX_VERSION,
                                   qrcodegen_Mask_AUTO, true);
    if (!ok) {
        /* Casi siempre significa que la URL es demasiado larga. */
        ESP_LOGE(TAG, "el texto no cabe en un QR de version %d: %d caracteres",
                 QR_MAX_VERSION, (int)strlen(text));
        return NULL;
    }

    int modulos = qrcodegen_getSize(qr_buf);
    int total   = modulos + 2 * QR_QUIET;

    /* Escala entera: media unidad de escala deforma los modulos y los lectores
     * fallan mas de lo que parece. */
    int escala = max_px / total;
    if (escala < 1) {
        ESP_LOGE(TAG, "no caben %d modulos en %d px", total, max_px);
        return NULL;
    }
    int lado = total * escala;

    lv_obj_t *canvas = lv_canvas_create(parent, NULL);
    if (canvas == NULL) {
        return NULL;
    }

    /* Libera el del QR anterior antes de pedir otro. */
    pos_qr_free();

    size_t bytes = LV_CANVAS_BUF_SIZE_TRUE_COLOR(lado, lado);
    void *px = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM);
    if (px == NULL) {
        ESP_LOGE(TAG, "sin memoria para el canvas (%d bytes)", (int)bytes);
        lv_obj_del(canvas);
        return NULL;
    }

    canvas_anterior = px;
    lv_canvas_set_buffer(canvas, px, lado, lado, LV_IMG_CF_TRUE_COLOR);
    /* Fondo blanco, incluida la zona tranquila. */
    lv_canvas_fill_bg(canvas, LV_COLOR_WHITE, LV_OPA_COVER);

    for (int y = 0; y < modulos; y++) {
        for (int x = 0; x < modulos; x++) {
            if (!qrcodegen_getModule(qr_buf, x, y)) {
                continue;
            }
            int px0 = (x + QR_QUIET) * escala;
            int py0 = (y + QR_QUIET) * escala;
            for (int dy = 0; dy < escala; dy++) {
                for (int dx = 0; dx < escala; dx++) {
                    lv_canvas_set_px(canvas, px0 + dx, py0 + dy, LV_COLOR_BLACK);
                }
            }
        }
    }

    ESP_LOGI(TAG, "QR de %d modulos a escala %d (%d px)", modulos, escala, lado);
    return canvas;
}


void pos_qr_free(void)
{
    if (canvas_anterior != NULL) {
        heap_caps_free(canvas_anterior);
        canvas_anterior = NULL;
    }
}
