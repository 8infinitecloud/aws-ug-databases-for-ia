# Modelo de datos — el adquirente

> **DEMO.** Este sistema no procesa pagos reales. No hay PAN, ni CVV real, ni
> conexión con ninguna red de tarjetas. Ver [../README.md](../README.md).

---

## Dónde estamos en la cadena de pagos

Esto es lo que determina todo el modelo, así que conviene fijarlo primero:

```
   Smart Coffee          FinCorp Payments         Red de tarjetas      Emisor
   (comercio)            (NOSOTROS)               (Visa/Mastercard)   (banco del
                                                                       cliente)
       │                        │                        │                │
   vende café          afilia comercios            enruta y liquida   autoriza
   opera el POS        provisiona terminales       entre bancos       contra el
                       autoriza y captura                            saldo
                       liquida al comercio
                       cobra MDR
```

**No somos el comercio.** Al comercio le importan productos, inventario, turnos y
caja. A nosotros nada de eso. A un adquirente le importa:

| Preocupación | Tabla |
|---|---|
| Afiliar comercios y hacerles KYC | `merchants` |
| Provisionar y vigilar terminales | `terminals` |
| Tokenizar instrumentos de pago | `payment_methods` |
| Autorizar y capturar | `payment_intents`, `charges` |
| **Llevar el libro mayor: bruto, comisión, neto** | `balance_transactions` |
| Liquidar al comercio | `payouts` |
| Devoluciones y contracargos | `refunds`, `disputes` |
| No cobrar dos veces cuando el POS reintenta | `idempotency_keys` |

> La tabla que de verdad define a un adquirente es **`balance_transactions`**. Un
> comercio no necesita un libro mayor de comisiones; nosotros vivimos de él.

---

## Modelo de referencia

Adaptado del modelo **[Stripe Payments de drawDB](https://www.drawdb.app/examples/stripe-payments)**
(13 tablas). Se usan 9 y se añaden dos cosas que Stripe no modela porque nace
*online-first*:

### 1. `terminals` — el dispositivo físico

Stripe empezó siendo una API para comercio electrónico: no hay terminal. Un
adquirente físico provisiona hardware con un **TID** (Terminal ID), y cada
transacción tiene que poder atribuirse a un dispositivo concreto.

En nuestro caso el `device_serial` es el serial del **elemento seguro ATECC608**
del M5Stack. Eso tiene una propiedad bonita: **el mismo valor identifica al
dispositivo en la base de datos y en AWS IoT Core**, porque el kit usa el serial
del chip como `clientId` MQTT. El dato queda atado al hardware que lo generó.

### 2. Vocabulario ISO 8583 — el mundo *card-present*

Los pagos con tarjeta presencial hablan [ISO 8583](https://en.wikipedia.org/wiki/ISO_8583).
`charges` incluye sus *data elements*:

| Campo | DE | Qué es |
|---|---|---|
| `mti` | — | Message Type Indicator. `0100` petición de autorización, `0110` respuesta |
| `stan` | DE 11 | System Trace Audit Number. Único por terminal y día |
| `rrn` | DE 37 | Retrieval Reference Number. **La referencia que se usa para localizar la operación en toda la cadena** — es la que pide el comercio cuando reclama y la que aparece en un contracargo |
| `response_code` | DE 39 | `00` aprobada · `05` no honrar · `51` fondos insuficientes · `54` vencida · `91` emisor no disponible |
| `auth_code` | DE 38 | Authorization Identification Response |
| `merchants.mcc` | DE 18 | Merchant Category Code ([ISO 18245](https://en.wikipedia.org/wiki/Merchant_category_code)). `5812` = restaurantes |
| `merchants.merchant_id` | DE 42 | Card Acceptor ID (MID) |
| `terminals.terminal_id` | DE 41 | Card Acceptor Terminal ID (TID) |

Usar estos nombres no es decoración: es el vocabulario con el que un equipo de
pagos habla, y hace que el modelo sea reconocible para quien viene del sector.

### Lo que se dejó fuera

`webhook_endpoints`, `webhook_events` y `webhook_deliveries`. Son el mecanismo por
el que el comercio se entera de lo que pasa, y en esta demo eso lo hace **AWS IoT
Core**: el POS se suscribe a un topic y recibe el resultado. Modelarlo dos veces
no aportaría nada.

---

## Diagrama

```
                          ┌───────────────┐
                          │   merchants   │  MID, MCC, risk_level, MDR
                          └───────┬───────┘
          ┌───────────────┬───────┼────────────┬──────────────┐
          │               │       │            │              │
   ┌──────▼─────┐  ┌──────▼─────┐ │   ┌────────▼───────┐  ┌───▼────┐
   │ terminals  │  │  payment_  │ │   │  balance_      │  │payouts │
   │ TID+serial │  │  methods   │ │   │  transactions  │◄─┤        │
   └──────┬─────┘  └──────┬─────┘ │   │ gross/fee/net  │  └────────┘
          │               │       │   └────────▲───────┘
          │        ┌──────▼───────▼──────┐     │
          └───────►│  payment_intents    │     │
                   │  amount, line_items │     │
                   └──────────┬──────────┘     │
                              │                │
                        ┌─────▼──────┐         │
                        │  charges   ├─────────┘
                        │ RRN, STAN, │
                        │ resp_code  │
                        └─────┬──────┘
                    ┌─────────┴─────────┐
              ┌─────▼─────┐      ┌──────▼─────┐
              │  refunds  │      │  disputes  │
              └───────────┘      └────────────┘

   idempotency_keys ──▶ merchants   (evita el doble cobro en reintentos)
```

---

## Dos decisiones que conviene entender

### El dinero es un entero

Todos los importes son `BIGINT` en **unidades menores**: S/ 18.50 se guarda como
`1850`. Las tasas van en **puntos básicos**: 2.99% de MDR es `299`.

No es pedantería. `0.1 + 0.2` no da `0.3` en coma flotante binaria, y en dinero
eso es un descuadre que aparece meses después en una liquidación. `NUMERIC`
serviría, pero el entero es más rápido y no admite ambigüedad sobre cuántos
decimales hay.

Es la convención del modelo de Stripe, y por eso los campos llevan sufijo
`_minor` y `_bps`: el nombre dice la unidad.

### La intención y el cobro son cosas distintas

```
payment_intents  ──▶  charges
"quiero cobrar        "el dinero se movió
 S/ 18.50"             y esto dijo el emisor"
```

Separarlos es lo que permite que el **QR exista antes del pago**: el POS crea la
intención, muestra el QR, y el `charge` aparece cuando alguien confirma desde su
móvil. Con una sola tabla habría que inventar estados raros para representar «hay
un QR en pantalla pero nadie ha pagado».

También permite pagos parciales y varios intentos sobre la misma intención, que es
lo que pasa cuando el primer CVV es incorrecto.

---

## Invariantes que garantiza la base de datos

Reglas de negocio que **no se delegan a la aplicación**, porque un `INSERT` que
falla es mejor que un descuadre que se descubre al liquidar:

| Constraint | Qué impide |
|---|---|
| `ledger_net_equals_gross_minus_fee` | Que el libro mayor no cuadre: `net = gross - fee`, siempre |
| `charges_capture_within_auth` | Capturar más de lo autorizado |
| `charges_refund_within_capture` | Devolver más de lo capturado |
| `charges_approved_has_auth` | Una transacción aprobada sin `response_code = '00'` ni código de autorización |
| `merchants_charges_need_kyc` | Cobrar por un comercio sin KYC completado |
| `payment_methods_no_pan` | Que `last_four` tenga más de 4 dígitos — barrera explícita contra guardar un PAN |
| `intents_received_within_amount` | Recibir más de lo que se pidió cobrar |
| `payouts_paid_has_timestamp` | Una liquidación marcada como pagada sin fecha de pago |

---

## La vista que consume el Lab 1

`v_transactions_for_ingestion` es lo que **DMS replica al data lake**. Una vista y
no las tablas crudas, por dos razones:

1. **Aísla el schema interno del consumidor.** Se puede refactorizar `charges` sin
   romper el pipeline de datos.
2. **Entrega el dato ya aplanado**, que es como lo quiere el Lab 2: una fila por
   transacción, con el comercio, el terminal y la comisión resueltos.

Sus columnas coinciden a propósito con el vocabulario que ya usan los Labs 2 y 3
(`amount`, `currency`, `status`, `risk_level`, `payment_method`), para que el
pipeline de calidad y el corpus de compliance sigan encajando sin cambios.

> Nótese que en la vista `merchant_id` se expone como **`customer_id`**. No es un
> error: desde nuestra posición, **el comercio es el cliente**. Es exactamente el
> cambio de perspectiva que hace que el resto del repositorio tenga sentido —
> FinCorp tiene clientes con `risk_level`, KYC y monitoreo AML, y esos clientes
> son comercios afiliados.

---

## Por qué esto encaja con el resto del workshop

El repositorio ya tenía un perfil de empresa de servicios financieros: clientes
con `risk_level`, un corpus de compliance con KYC, AML y monitoreo transaccional,
y umbrales de reporte por moneda.

**Ese perfil es el de un adquirente.** Un adquirente hace KYC de sus comercios,
monitorea sus transacciones buscando patrones de lavado, y suspende a los que
acumulan contracargos. Las preguntas que responde el asistente del Lab 3 —
*«¿qué hago con un WITHDRAWAL de un cliente con risk_level HIGH?»* — son
literalmente las de un analista de monitoreo de un adquirente.

Con el modelo de comercio que había escrito antes (productos, ventas, turnos) esa
conexión no existía. Con el modelo de adquirente, el workshop cuenta una sola
historia de principio a fin.

---

## Ficheros

| Fichero | Qué |
|---|---|
| `001_schema.sql` | DDL. Idempotente, se puede reejecutar |
| `002_seed.sql` | Smart Coffee como comercio afiliado, su terminal y la tarjeta de demo |

---

## Fuentes

- [Stripe Payments — modelo de referencia (drawDB)](https://www.drawdb.app/examples/stripe-payments)
- [ISO 8583 — Wikipedia](https://en.wikipedia.org/wiki/ISO_8583)
- [Merchant Category Code (ISO 18245)](https://en.wikipedia.org/wiki/Merchant_category_code)
- [ISO 8583 Reference Guide — Worldpay](https://developerengine.fisglobal.com/assets/pdf/Worldpay_ISO_8583_Reference_Guide_V2.46.pdf)
