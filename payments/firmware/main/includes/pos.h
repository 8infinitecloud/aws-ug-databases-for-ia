/*
 * Terminal POS de FinCorp — tipos compartidos.
 *
 * DEMO EDUCATIVA. Este terminal no procesa pagos reales: no lee tarjetas, no
 * hay PAN, y el CVV que pide es un valor de prueba configurable. La
 * autorizacion la simula el backend.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

#define POS_MAX_ITEMS        4
#define POS_REF_LEN          24
#define POS_CURRENCY         "PEN"

/* Una linea del carrito. Al pago no le hace falta; a la analitica si. */
typedef struct {
    const char *sku;
    const char *name;
    int         unit_price_minor;
    int         quantity;
} pos_item_t;

/* El carrito y la intencion de cobro que lo representa. */
typedef struct {
    char       intent_reference[POS_REF_LEN];
    int        amount_minor;
    pos_item_t items[POS_MAX_ITEMS];
    int        item_count;
} pos_cart_t;

/*
 * El resultado de la autorizacion.
 *
 * Lleva los data elements de ISO 8583 porque son lo que se imprime en el
 * comprobante: el RRN (DE 37) es la referencia con la que se reclama.
 */
typedef struct {
    bool approved;
    char status[16];
    char response_code[4];   /* DE 39 */
    char auth_code[16];      /* DE 38 */
    char rrn[16];            /* DE 37 */
    int  amount_minor;
    int  fee_minor;
    int  net_minor;
    char decline_reason[72];
} pos_charge_t;

/* Importe en centimos -> "18.50". */
void pos_format_amount(int minor, char *out, size_t len);
