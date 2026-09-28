/*
 * La interfaz del POS.
 *
 * Dos hilos y una regla: LVGL no es reentrante, asi que todo lo que toque la
 * pantalla va entre xSemaphoreTake(xGuiSemaphore) y su Give. Las llamadas a la
 * API viven en una tarea aparte porque tardan cientos de milisegundos y
 * hacerlas en el hilo de LVGL congelaria la pantalla justo cuando el cliente
 * esta mirando.
 */

#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"

#include "core2forAWS.h"

#include "pos.h"
#include "pos_api.h"
#include "pos_qr.h"
#include "pos_ui.h"

static const char *TAG = "pos_ui";

#define QR_TIMEOUT_S   120
#define QR_POLL_MS     2000

/* El catalogo del comercio. Vive en el POS y no en el adquirente a proposito:
 * que vende el comercio no es asunto nuestro; lo nuestro es el importe. */
static const pos_item_t CATALOGO[] = {
    { .sku = "ESP", .name = "Espresso", .unit_price_minor = 800  },
    { .sku = "LAT", .name = "Latte",    .unit_price_minor = 1850 },
    { .sku = "CRO", .name = "Croissant", .unit_price_minor = 1200 },
};
#define CATALOGO_N (sizeof(CATALOGO) / sizeof(CATALOGO[0]))

static lv_obj_t *pantalla;
static lv_obj_t *barra_wifi;
static lv_obj_t *lbl_total;
static lv_obj_t *lbl_estado;
static lv_obj_t *ta_cvv;
static lv_obj_t *btn_producto[CATALOGO_N];
static lv_obj_t *btn_qr;

static pos_cart_t carrito;
static pos_charge_t cobro;
static char cvv[8];

static void ver_catalogo(void);
static void ver_metodo(void);
static void ver_cvv(void);
static void ver_resultado(void);

/* ── Utilidades de pantalla ─────────────────────────────────────────────── */

static void limpiar(void)
{
    lv_obj_clean(pantalla);
    barra_wifi = NULL;
    lbl_total = lbl_estado = ta_cvv = NULL;
    btn_qr = NULL;
    memset(btn_producto, 0, sizeof(btn_producto));
}

static lv_obj_t *titulo(const char *txt)
{
    lv_obj_t *l = lv_label_create(pantalla, NULL);
    lv_label_set_text(l, txt);
    lv_obj_align(l, NULL, LV_ALIGN_IN_TOP_MID, 0, 6);
    return l;
}

/*
 * Crea un boton.
 *
 * No lleva dato asociado porque este LVGL esta compilado sin LV_USE_USER_DATA:
 * la opcion de Kconfig que lo activaria no existe en el componente del kit. Los
 * botones que necesitan distinguirse se identifican por su puntero.
 */
static lv_obj_t *boton(const char *txt, lv_coord_t x, lv_coord_t y,
                       lv_coord_t w, lv_coord_t h, lv_event_cb_t cb)
{
    lv_obj_t *b = lv_btn_create(pantalla, NULL);
    lv_obj_set_pos(b, x, y);
    lv_obj_set_size(b, w, h);
    lv_obj_set_event_cb(b, cb);

    lv_obj_t *l = lv_label_create(b, NULL);
    lv_label_set_text(l, txt);
    return b;
}

static void refrescar_total(void)
{
    if (lbl_total == NULL) {
        return;
    }
    char imp[16];
    pos_format_amount(carrito.amount_minor, imp, sizeof(imp));

    char txt[48];
    snprintf(txt, sizeof(txt), "Total  %s %s", imp, POS_CURRENCY);
    lv_label_set_text(lbl_total, txt);
    lv_obj_align(lbl_total, NULL, LV_ALIGN_IN_TOP_MID, 0, 34);
}

static void estado(const char *txt)
{
    if (lbl_estado == NULL) {
        return;
    }
    lv_label_set_text(lbl_estado, txt);
    lv_obj_align(lbl_estado, NULL, LV_ALIGN_IN_BOTTOM_MID, 0, -8);
}

/* ── Pantalla 1: el catalogo ────────────────────────────────────────────── */

static void on_producto(lv_obj_t *obj, lv_event_t e)
{
    if (e != LV_EVENT_CLICKED) {
        return;
    }
    int idx = -1;
    for (int i = 0; i < (int)CATALOGO_N; i++) {
        if (btn_producto[i] == obj) {
            idx = i;
            break;
        }
    }
    if (idx < 0) {
        return;
    }
    const pos_item_t *prod = &CATALOGO[idx];

    /* Si ya esta en el carrito sube la cantidad; si no, se anade. */
    for (int i = 0; i < carrito.item_count; i++) {
        if (strcmp(carrito.items[i].sku, prod->sku) == 0) {
            carrito.items[i].quantity++;
            carrito.amount_minor += prod->unit_price_minor;
            refrescar_total();
            return;
        }
    }
    if (carrito.item_count >= POS_MAX_ITEMS) {
        estado("Carrito lleno");
        return;
    }
    carrito.items[carrito.item_count] = *prod;
    carrito.items[carrito.item_count].quantity = 1;
    carrito.item_count++;
    carrito.amount_minor += prod->unit_price_minor;
    refrescar_total();
}

static void on_cobrar(lv_obj_t *obj, lv_event_t e)
{
    (void)obj;
    if (e != LV_EVENT_CLICKED) {
        return;
    }
    if (carrito.amount_minor <= 0) {
        estado("Anade algo primero");
        return;
    }
    ver_metodo();
}

static void ver_catalogo(void)
{
    memset(&carrito, 0, sizeof(carrito));
    memset(&cobro, 0, sizeof(cobro));
    cvv[0] = '\0';

    limpiar();
    titulo("FinCorp POS  -  DEMO");

    lbl_total = lv_label_create(pantalla, NULL);
    refrescar_total();

    lv_coord_t y = 62;
    for (int i = 0; i < (int)CATALOGO_N; i++) {
        char txt[48];
        char precio[16];
        pos_format_amount(CATALOGO[i].unit_price_minor, precio, sizeof(precio));
        snprintf(txt, sizeof(txt), "%s   %s", CATALOGO[i].name, precio);
        btn_producto[i] = boton(txt, 12, y, 180, 38, on_producto);
        y += 44;
    }

    boton("COBRAR", 204, 62, 104, 126, on_cobrar);

    lbl_estado = lv_label_create(pantalla, NULL);
    estado("Sin pagos reales");
}

/* ── Pantalla 2: el metodo ──────────────────────────────────────────────── */

/*
 * Crea la intencion de cobro contra el adquirente.
 *
 * Va en su propia tarea porque es una llamada de red: en el hilo de LVGL
 * dejaria la pantalla sin responder mientras dura.
 */
static void tarea_crear_intencion(void *arg)
{
    bool es_qr = (bool)(intptr_t)arg;

    pos_api_new_reference(carrito.intent_reference, sizeof(carrito.intent_reference));
    esp_err_t err = pos_api_create_intent(&carrito);

    xSemaphoreTake(xGuiSemaphore, portMAX_DELAY);
    if (err != ESP_OK) {
        estado("No se pudo crear el cobro");
    } else if (es_qr) {
        /* La pantalla del QR se construye aqui, ya con la referencia. */
        limpiar();
        titulo("Escanea para pagar");

        char url[160];
        pos_api_payment_url(carrito.intent_reference, url, sizeof(url));

        lv_obj_t *qr = pos_qr_draw(pantalla, url, 170);
        if (qr == NULL) {
            estado("No se pudo generar el QR");
        } else {
            lv_obj_align(qr, NULL, LV_ALIGN_IN_TOP_MID, 0, 28);
        }

        char imp[16];
        pos_format_amount(carrito.amount_minor, imp, sizeof(imp));
        char txt[48];
        snprintf(txt, sizeof(txt), "%s %s", imp, POS_CURRENCY);
        lv_obj_t *l = lv_label_create(pantalla, NULL);
        lv_label_set_text(l, txt);
        lv_obj_align(l, NULL, LV_ALIGN_IN_BOTTOM_MID, 0, -26);

        lbl_estado = lv_label_create(pantalla, NULL);
        estado("Esperando pago...");
    } else {
        ver_cvv();
    }
    xSemaphoreGive(xGuiSemaphore);

    if (err == ESP_OK && es_qr) {
        /* Sondear y no escuchar MQTT: mantener la conexion a IoT Core abierta
         * mientras se muestra el QR cuesta memoria y una reconexion a mitad de
         * la venta seria peor que preguntar cada dos segundos. */
        char st[24] = "";
        for (int s = 0; s < QR_TIMEOUT_S * 1000 / QR_POLL_MS; s++) {
            vTaskDelay(pdMS_TO_TICKS(QR_POLL_MS));
            if (pos_api_poll_intent(carrito.intent_reference, st, sizeof(st)) != ESP_OK) {
                continue;
            }
            if (strcmp(st, "SUCCEEDED") == 0) {
                pos_api_fetch_charge(carrito.intent_reference, &cobro);
                xSemaphoreTake(xGuiSemaphore, portMAX_DELAY);
                ver_resultado();
                xSemaphoreGive(xGuiSemaphore);
                vTaskDelete(NULL);
                return;
            }
        }
        xSemaphoreTake(xGuiSemaphore, portMAX_DELAY);
        estado("El QR expiro");
        xSemaphoreGive(xGuiSemaphore);
    }

    vTaskDelete(NULL);
}

static void on_metodo(lv_obj_t *obj, lv_event_t e)
{
    /*
     * El filtro por LV_EVENT_CLICKED no es decorativo. Un solo toque genera
     * siete eventos —PRESSED, PRESSING x2, SHORT_CLICKED, CLICKED, RELEASED y
     * alguno mas— y sin el filtro cada toque crearia siete intenciones de cobro.
     * Verificado en el dispositivo el 2026-09-28: con el filtro, un toque
     * produce exactamente una.
     */
    if (e != LV_EVENT_CLICKED) {
        return;
    }
    bool es_qr = (obj == btn_qr);
    estado("Creando el cobro...");

    /* 6 KB de pila: el cliente HTTP con TLS necesita holgura. */
    xTaskCreatePinnedToCore(tarea_crear_intencion, "crear_intencion", 6144,
                            (void *)(intptr_t)es_qr, 3, NULL, 1);
}

static void on_cancelar(lv_obj_t *obj, lv_event_t e)
{
    (void)obj;
    if (e == LV_EVENT_CLICKED) {
        ver_catalogo();
    }
}

static void ver_metodo(void)
{
    limpiar();
    titulo("Como quieres pagar?");

    lbl_total = lv_label_create(pantalla, NULL);
    refrescar_total();

    boton("TARJETA", 20, 80, 130, 70, on_metodo);
    btn_qr = boton("QR", 170, 80, 130, 70, on_metodo);
    boton("Cancelar", 110, 165, 100, 34, on_cancelar);

    lbl_estado = lv_label_create(pantalla, NULL);
    estado("");
}

/* ── Pantalla 3: el CVV (simulado) ──────────────────────────────────────── */

static void tarea_confirmar(void *arg)
{
    (void)arg;
    esp_err_t err = pos_api_confirm_card(carrito.intent_reference, cvv, &cobro);

    xSemaphoreTake(xGuiSemaphore, portMAX_DELAY);
    if (err != ESP_OK) {
        estado("Error al confirmar");
    } else {
        ver_resultado();
    }
    xSemaphoreGive(xGuiSemaphore);

    vTaskDelete(NULL);
}

static void on_tecla(lv_obj_t *obj, lv_event_t e)
{
    if (e != LV_EVENT_VALUE_CHANGED) {
        return;
    }
    const char *txt = lv_btnmatrix_get_active_btn_text(obj);
    if (txt == NULL) {
        return;
    }

    size_t n = strlen(cvv);
    if (strcmp(txt, LV_SYMBOL_BACKSPACE) == 0) {
        if (n > 0) {
            cvv[n - 1] = '\0';
        }
    } else if (strcmp(txt, LV_SYMBOL_OK) == 0) {
        if (n < 3) {
            estado("El CVV son 3 digitos");
            return;
        }
        estado("Autorizando...");
        xTaskCreatePinnedToCore(tarea_confirmar, "confirmar", 6144, NULL, 3, NULL, 1);
        return;
    } else if (n < 4) {
        cvv[n] = txt[0];
        cvv[n + 1] = '\0';
    }

    /* Se muestran asteriscos: el CVV no se ensena ni se registra en el log. */
    char mask[8] = "";
    for (size_t i = 0; i < strlen(cvv); i++) {
        mask[i] = '*';
        mask[i + 1] = '\0';
    }
    lv_label_set_text(ta_cvv, mask[0] ? mask : "___");
    lv_obj_align(ta_cvv, NULL, LV_ALIGN_IN_TOP_MID, 0, 36);
}

static const char *TECLAS[] = {
    "1", "2", "3", "\n",
    "4", "5", "6", "\n",
    "7", "8", "9", "\n",
    LV_SYMBOL_BACKSPACE, "0", LV_SYMBOL_OK, ""
};

static void ver_cvv(void)
{
    limpiar();
    titulo("CVV  (DEMO: 123)");

    ta_cvv = lv_label_create(pantalla, NULL);
    lv_label_set_text(ta_cvv, "___");
    lv_obj_align(ta_cvv, NULL, LV_ALIGN_IN_TOP_MID, 0, 36);

    lv_obj_t *kb = lv_btnmatrix_create(pantalla, NULL);
    lv_btnmatrix_set_map(kb, TECLAS);
    lv_obj_set_size(kb, 200, 150);
    lv_obj_align(kb, NULL, LV_ALIGN_IN_BOTTOM_MID, 0, -24);
    lv_obj_set_event_cb(kb, on_tecla);

    lbl_estado = lv_label_create(pantalla, NULL);
    estado("");
}

/* ── Pantalla 4: el resultado ───────────────────────────────────────────── */

static void ver_resultado(void)
{
    limpiar();

    lv_obj_t *icono = lv_label_create(pantalla, NULL);
    lv_label_set_text(icono, cobro.approved ? LV_SYMBOL_OK : LV_SYMBOL_CLOSE);
    lv_obj_align(icono, NULL, LV_ALIGN_IN_TOP_MID, 0, 12);

    lv_obj_t *t = lv_label_create(pantalla, NULL);
    lv_label_set_text(t, cobro.approved ? "APROBADA" : "RECHAZADA");
    lv_obj_align(t, NULL, LV_ALIGN_IN_TOP_MID, 0, 40);
    lv_obj_set_style_local_text_color(t, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT,
                                      cobro.approved ? LV_COLOR_LIME : LV_COLOR_RED);

    char imp[16];
    pos_format_amount(cobro.amount_minor, imp, sizeof(imp));

    char detalle[160];
    if (cobro.approved) {
        /* El RRN es lo que se busca cuando el comercio reclama un contracargo. */
        snprintf(detalle, sizeof(detalle),
                 "%s %s\nAut: %s\nRRN: %s",
                 imp, POS_CURRENCY, cobro.auth_code, cobro.rrn);
    } else {
        snprintf(detalle, sizeof(detalle),
                 "%s %s\nCodigo: %s\n%s",
                 imp, POS_CURRENCY, cobro.response_code, cobro.decline_reason);
    }

    lv_obj_t *d = lv_label_create(pantalla, NULL);
    lv_label_set_long_mode(d, LV_LABEL_LONG_BREAK);
    lv_obj_set_width(d, 280);
    lv_label_set_align(d, LV_LABEL_ALIGN_CENTER);
    lv_label_set_text(d, detalle);
    lv_obj_align(d, NULL, LV_ALIGN_CENTER, 0, 10);

    boton("NUEVA VENTA", 90, 195, 140, 36, on_cancelar);
}

/* ── Arranque ───────────────────────────────────────────────────────────── */

void pos_ui_set_wifi(bool connected)
{
    xSemaphoreTake(xGuiSemaphore, portMAX_DELAY);
    if (barra_wifi == NULL) {
        barra_wifi = lv_label_create(pantalla, NULL);
    }
    lv_label_set_text(barra_wifi, connected ? LV_SYMBOL_WIFI : LV_SYMBOL_WARNING);
    lv_obj_align(barra_wifi, NULL, LV_ALIGN_IN_TOP_RIGHT, -6, 6);
    xSemaphoreGive(xGuiSemaphore);
}

void pos_ui_start(void)
{
    xSemaphoreTake(xGuiSemaphore, portMAX_DELAY);
    pantalla = lv_scr_act();
    ver_catalogo();
    xSemaphoreGive(xGuiSemaphore);

    ESP_LOGI(TAG, "interfaz lista");
}
