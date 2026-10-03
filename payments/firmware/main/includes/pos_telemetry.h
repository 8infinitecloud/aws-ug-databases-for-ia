/*
 * Telemetria del terminal hacia AWS IoT Core.
 *
 * El cobro va por HTTP, como en Stripe Terminal; esto NO toca el dinero. Es el
 * latido del dispositivo —bateria, senal, memoria, latencia de la API, QR
 * abandonados— que ninguna base de datos guarda y que dice POR QUE fallo un
 * cobro, no solo que fallo.
 *
 * Se publica en <serial del ATECC608>/telemetry, que es el unico prefijo que la
 * politica de IoT del dispositivo le permite. Desactivado por defecto
 * (menuconfig: FinCorp POS > Telemetria): no cambia nada del terminal validado
 * hasta que se active a proposito.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    POS_QR_SHOWN,
    POS_QR_PAID,
    POS_QR_CANCELED,
    POS_QR_EXPIRED,
} pos_qr_event_t;

/* Lanza la tarea de telemetria si esta activada. Llamar tras la WiFi. */
void pos_telemetry_start(void);

/* Una peticion a la API terminada: codigo HTTP (0 si no hubo respuesta),
 * duracion y si fue por timeout. Seguro desde cualquier tarea. */
void pos_telemetry_note_http(int status, int64_t elapsed_ms, bool timeout);

/* Un paso del ciclo de vida del QR. Seguro desde cualquier tarea. */
void pos_telemetry_note_qr(pos_qr_event_t ev);

/* Hay una venta en curso: el latido espera para no abrir un segundo TLS en
 * mitad del cobro. */
void pos_telemetry_set_busy(bool busy);
