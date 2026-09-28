/*
 * Cliente de la API de adquirencia.
 *
 * DEMO: el CVV que se envia es un valor de prueba. No hay tarjeta fisica, ni
 * PAN, ni lectura de banda o chip. El token de tarjeta es ficticio.
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include "esp_log.h"
#include "esp_http_client.h"
#include "esp_system.h"   /* esp_random() */
#include "cJSON.h"

#include "pos.h"
#include "pos_api.h"

static const char *TAG = "pos_api";

#define API_BASE      CONFIG_API_BASE_URL
#define TERMINAL_ID   CONFIG_TERMINAL_ID
#define RESP_MAX      2048
#define HTTP_TIMEOUT  10000

/* La CA raiz de Amazon, empotrada por board_build.embed_txtfiles. */
extern const uint8_t aws_root_ca_pem_start[] asm("_binary_aws_root_ca_pem_start");

/* Acumula el cuerpo de la respuesta entre eventos del cliente HTTP. */
typedef struct {
    char  *buf;
    int    len;
    int    cap;
} resp_t;

static esp_err_t on_http_event(esp_http_client_event_t *evt)
{
    resp_t *r = (resp_t *)evt->user_data;

    if (evt->event_id == HTTP_EVENT_ON_DATA && r != NULL) {
        /* Se trunca en vez de crecer: una respuesta mayor que RESP_MAX solo
         * puede ser un error, y en un microcontrolador no interesa realojar. */
        int cabe = r->cap - r->len - 1;
        int copia = evt->data_len < cabe ? evt->data_len : cabe;
        if (copia > 0) {
            memcpy(r->buf + r->len, evt->data, copia);
            r->len += copia;
            r->buf[r->len] = '\0';
        }
    }
    return ESP_OK;
}

/*
 * Hace la peticion y devuelve el cuerpo. `body` NULL significa GET.
 *
 * El llamante aporta el buffer: asi no hay malloc que olvidarse de liberar en
 * los caminos de error, que en C son la mayoria.
 */
static esp_err_t request(const char *method, const char *path, const char *body,
                         const char *idem_key, char *out, int out_cap, int *status_out)
{
    char url[256];
    snprintf(url, sizeof(url), "%s%s", API_BASE, path);

    resp_t resp = { .buf = out, .len = 0, .cap = out_cap };
    out[0] = '\0';

    esp_http_client_config_t cfg = {
        .url           = url,
        .event_handler = on_http_event,
        .user_data     = &resp,
        .timeout_ms    = HTTP_TIMEOUT,
        .cert_pem      = (const char *)aws_root_ca_pem_start,
    };

    esp_http_client_handle_t cli = esp_http_client_init(&cfg);
    if (cli == NULL) {
        ESP_LOGE(TAG, "no se pudo crear el cliente HTTP");
        return ESP_FAIL;
    }

    esp_http_client_set_method(cli, strcmp(method, "POST") == 0
                                        ? HTTP_METHOD_POST : HTTP_METHOD_GET);
    if (body != NULL) {
        esp_http_client_set_header(cli, "Content-Type", "application/json");
        esp_http_client_set_post_field(cli, body, strlen(body));
    }
    if (idem_key != NULL) {
        /* Sin esto, un reintento tras un timeout cobraria dos veces. */
        esp_http_client_set_header(cli, "Idempotency-Key", idem_key);
    }

    esp_err_t err = esp_http_client_perform(cli);
    int status = esp_http_client_get_status_code(cli);
    esp_http_client_cleanup(cli);

    if (status_out != NULL) {
        *status_out = status;
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "%s %s fallo: %s", method, path, esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "%s %s -> %d", method, path, status);
    return ESP_OK;
}

void pos_api_new_reference(char *out, size_t len)
{
    /* Suficientemente unica para una demo y corta para que el QR no se vuelva
     * denso: cada caracter de mas sube la version del QR, y un QR denso en una
     * pantalla de 2 pulgadas no se escanea desde el publico. */
    snprintf(out, len, "PI%08X", (unsigned int)esp_random());
}

void pos_api_payment_url(const char *ref, char *out, size_t len)
{
    snprintf(out, len, "%s/pay/%s", API_BASE, ref);
}

esp_err_t pos_api_create_intent(pos_cart_t *cart)
{
    char body[512];
    int n = snprintf(body, sizeof(body),
        "{\"intent_reference\":\"%s\",\"terminal_id\":\"%s\","
        "\"amount_minor\":%d,\"currency_code\":\"%s\",\"line_items\":[",
        cart->intent_reference, TERMINAL_ID, cart->amount_minor, POS_CURRENCY);

    for (int i = 0; i < cart->item_count; i++) {
        pos_item_t *it = &cart->items[i];
        n += snprintf(body + n, sizeof(body) - n,
            "%s{\"sku\":\"%s\",\"name\":\"%s\",\"quantity\":%d,\"unit_price_minor\":%d}",
            i ? "," : "", it->sku, it->name, it->quantity, it->unit_price_minor);
    }
    snprintf(body + n, sizeof(body) - n, "]}");

    char resp[RESP_MAX];
    int status = 0;
    char idem[POS_REF_LEN + 8];
    snprintf(idem, sizeof(idem), "new-%s", cart->intent_reference);

    esp_err_t err = request("POST", "/v1/intents", body, idem, resp, sizeof(resp), &status);
    if (err != ESP_OK) {
        return err;
    }
    if (status != 201 && status != 200) {
        ESP_LOGE(TAG, "crear intencion devolvio %d: %s", status, resp);
        return ESP_FAIL;
    }
    return ESP_OK;
}

/* Vuelca un campo de texto del JSON al destino, dejandolo vacio si no viene. */
static void json_str(const cJSON *root, const char *campo, char *dst, size_t len)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(root, campo);
    if (cJSON_IsString(v) && v->valuestring != NULL) {
        snprintf(dst, len, "%s", v->valuestring);
    } else {
        dst[0] = '\0';
    }
}

static int json_int(const cJSON *root, const char *campo)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(root, campo);
    return cJSON_IsNumber(v) ? v->valueint : 0;
}

static esp_err_t parse_charge(const char *json, pos_charge_t *out)
{
    cJSON *root = cJSON_Parse(json);
    if (root == NULL) {
        ESP_LOGE(TAG, "respuesta no es JSON: %s", json);
        return ESP_FAIL;
    }

    memset(out, 0, sizeof(*out));
    json_str(root, "status",         out->status,         sizeof(out->status));
    json_str(root, "response_code",  out->response_code,  sizeof(out->response_code));
    json_str(root, "auth_code",      out->auth_code,      sizeof(out->auth_code));
    json_str(root, "rrn",            out->rrn,            sizeof(out->rrn));
    json_str(root, "decline_reason", out->decline_reason, sizeof(out->decline_reason));
    out->amount_minor = json_int(root, "amount_minor");
    out->fee_minor    = json_int(root, "fee_minor");
    out->net_minor    = json_int(root, "net_minor");

    /* El 00 de ISO 8583 es lo unico que aprueba. */
    out->approved = strcmp(out->response_code, "00") == 0;

    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t pos_api_confirm_card(const char *ref, const char *cvv, pos_charge_t *out)
{
    char path[80];
    snprintf(path, sizeof(path), "/v1/intents/%s/confirm", ref);

    /* DEMO: token ficticio. Un adquirente real nunca veria aqui un PAN. */
    char body[192];
    snprintf(body, sizeof(body),
        "{\"channel\":\"CARD_PRESENT\",\"card_token\":\"demo_card_4242\",\"cvv\":\"%s\"}",
        cvv);

    char idem[POS_REF_LEN + 8];
    snprintf(idem, sizeof(idem), "cnf-%s", ref);

    char resp[RESP_MAX];
    int status = 0;
    esp_err_t err = request("POST", path, body, idem, resp, sizeof(resp), &status);
    if (err != ESP_OK) {
        return err;
    }
    /* Una declinacion llega con 200: el codigo va en el cuerpo. Solo un 4xx o
     * 5xx es un fallo de verdad. */
    if (status >= 400) {
        ESP_LOGE(TAG, "confirmar devolvio %d: %s", status, resp);
        return ESP_FAIL;
    }
    return parse_charge(resp, out);
}

esp_err_t pos_api_poll_intent(const char *ref, char *status_out, size_t status_len)
{
    char path[64];
    snprintf(path, sizeof(path), "/v1/intents/%s", ref);

    char resp[RESP_MAX];
    int status = 0;
    esp_err_t err = request("GET", path, NULL, NULL, resp, sizeof(resp), &status);
    if (err != ESP_OK || status != 200) {
        return ESP_FAIL;
    }

    cJSON *root = cJSON_Parse(resp);
    if (root == NULL) {
        return ESP_FAIL;
    }
    json_str(root, "status", status_out, status_len);
    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t pos_api_fetch_charge(const char *ref, pos_charge_t *out)
{
    /*
     * Reconfirmar una intencion ya cobrada devuelve el cobro que ya existe, sin
     * cobrar otra vez: lo garantizan la clave de idempotencia y, por debajo, la
     * comprobacion de estado del backend. Asi el POS recupera los datos del
     * comprobante sin una ruta aparte.
     */
    return pos_api_confirm_card(ref, CONFIG_DEMO_CVV, out);
}

void pos_format_amount(int minor, char *out, size_t len)
{
    snprintf(out, len, "%d.%02d", minor / 100, abs(minor % 100));
}
