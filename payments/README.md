# FinCorp Payments — plataforma de adquirencia

> **DEMO EDUCATIVA — NO PROCESA PAGOS REALES.**
>
> No hay integración con Visa, Mastercard, Yape, Plin ni ningún procesador. El
> único instrumento es un token ficticio, `demo_card_4242`, y los códigos de
> autorización llevan prefijo `MOCK-`. Ver [Seguridad](#seguridad).

---

## Quién es quién

Esto condiciona todo el diseño, y es fácil confundirlo:

| | Quién | Qué hace |
|---|---|---|
| **FinCorp Payments** | **Nosotros** | Adquirente. Afilia comercios, autoriza, captura, liquida y cobra MDR |
| **Smart Coffee** | Un comercio afiliado | Vende café. Es **nuestro cliente**, no nosotros |

```
Smart Coffee          FinCorp Payments        Red de tarjetas      Emisor
(comercio,            (NOSOTROS:              (Visa/Mastercard)   (banco del
 nuestro cliente)      adquirente)                                 cliente)
     │                       │                       │                │
 vende café           afilia comercios          enruta y         autoriza
 opera el POS         autoriza y captura        liquida entre     contra el
 que le dimos         liquida al comercio       bancos           saldo
                      cobra MDR
```

Por eso este directorio se llama `payments/` y no `smart-coffee/`: contiene
**nuestra** plataforma. Smart Coffee aparece dentro, como el comercio de los datos
de demo y como dueño del POS físico que se usa en la charla.

> FinCorp es la empresa ficticia que ya usa todo el workshop: sus clientes con
> `risk_level`, su corpus de KYC y AML, su monitoreo transaccional. **Ese perfil
> es el de un adquirente**, y es lo que une esta pieza con los tres labs.

---

## Estructura

| Directorio | Qué |
|---|---|
| `schema/` | Modelo transaccional del adquirente (PostgreSQL 16 sobre EC2, el core "on-premises") |
| `backend/` | API de adquirencia sobre Fargate |
| `web/` | Página de pago QR (`/pay/{ref}`) y el banco simulado de 3-D Secure (`/3ds/{ref}`), servidas por la propia API |
| `firmware/` | Firmware del terminal (M5Stack Core2 for AWS IoT Kit), compilación local |

La infraestructura está en [`../infra/payments/`](../infra/payments/).

---

## Estado

| Componente | Estado |
|---|---|
| Schema transaccional | ✅ Validado contra PostgreSQL 16; se aplica limpio desde cero y se reaplica |
| Canal online (PaymentIntents estilo Stripe, 3DS, claves de API) | ✅ 30 tests contra PostgreSQL real |
| Dominio de adquirencia (autorizar, capturar, libro mayor) | ✅ Probado contra PostgreSQL real |
| API HTTP + idempotencia | ✅ Tests de integración · imagen construida |
| Generador de tráfico y cartera de 11 comercios | ✅ Probado por HTTP contra la API |
| Terraform (EC2, Fargate, API Gateway) | ✅ `validate` · arranque de la base probado en Amazon Linux 2023 |
| Web de pago QR | ✅ Validada en el dispositivo y en AWS (2026-09-28) |
| Firmware del terminal | ✅ Grabado y validado de punta a punta (2026-09-28) |
| Telemetría del firmware | ⚠️ Compila · **sin probar en el dispositivo**, desactivada por defecto |

---

## Seguridad

Lo que este sistema **no** hace:

- No procesa pagos reales ni se conecta a ninguna red de tarjetas
- No almacena PAN. El schema tiene una barrera explícita: `last_four` es `CHAR(4)`
- No usa CVV reales. El del mock es un valor configurable por entorno
- No guarda claves de API en claro, solo su hash SHA-256

Lo que **sí** hay que proteger, porque la infraestructura es real:

- La contraseña de la base va en Secrets Manager, nunca en el código
- Las claves de API de las tiendas de la demo no están en el repositorio: las
  crea `scripts/provision_demo_keys.py` contra tu base, en un fichero ignorado
- El state de Terraform guarda contraseñas en claro y está en `.gitignore`
- La clave privada del terminal vive en el elemento seguro ATECC608 y no se puede
  extraer: no hay secreto que versionar

---

## La API

Sigue el ciclo de vida de un PaymentIntent de Stripe
([docs](https://docs.stripe.com/payments/paymentintents/lifecycle)), con dos
clientes que se identifican distinto: **el POS por su terminal**, y **la tienda
online por su clave de API** (`Authorization: Bearer sk_demo_…`).

| | Quién la llama | Qué hace |
|---|---|---|
| `POST /v1/intents` | el POS (con `terminal_id`) o la tienda (con su clave) | crea la intención |
| `GET /v1/intents/{ref}` | la web del QR | lee importe y comercio antes de confirmar |
| `POST /v1/intents/{ref}/confirm` | POS, web del QR o tienda | autoriza, captura y asienta en el libro mayor |
| `POST /v1/intents/{ref}/cancel` | POS o tienda | abandona sin cobrar |
| `POST /v1/refunds` | el comercio, con su clave | devuelve lo cobrado, entero o en parte |
| `GET /pay/{ref}` | el móvil que escanea | la página de pago del QR |
| `GET /3ds/{ref}` | el navegador del comprador | el banco simulado que autentica (3-D Secure) |
| `POST /v1/intents/{ref}/authenticate` | esa página | el resultado de la autenticación |

Más `GET /health`, que es lo que consulta el health check del ALB.

### Los estados, y el canal online

```
REQUIRES_PAYMENT_METHOD ──confirm──┬─► SUCCEEDED
        ▲                          ├─► REQUIRES_ACTION ──3DS──┬─► SUCCEEDED
        └──── rechazo (05/51/54) ──┘                          └─► (fallido) vuelve arriba
REQUIRES_CONFIRMATION (POS: el QR ya está en pantalla)       CANCELED
```

Una declinación **vuelve a `REQUIRES_PAYMENT_METHOD`**: el comprador reintenta
con otra tarjeta sobre la misma intención, y cada intento deja su cobro
(`ch_<ref>_1`, `ch_<ref>_2`...).

En el canal online el emisor simulado decide por **PaymentMethods de prueba**,
con los identificadores que documenta Stripe. Ningún número de tarjeta en ninguna
parte:

| `payment_method` | Resultado |
|---|---|
| `pm_card_visa` | aprobada (`00`) |
| `pm_card_visa_chargeDeclined` | `05` |
| `pm_card_visa_chargeDeclinedInsufficientFunds` | `51` |
| `pm_card_chargeDeclinedExpiredCard` | `54` |
| `pm_card_threeDSecure2Required` | `REQUIRES_ACTION`, y `00` si el comprador se autentica |

Una **devolución** no cambia el estado del cobro, como en Stripe: el cobro sigue
capturado y acumula `amount_refunded`, y el libro mayor recibe un asiento
negativo por el bruto (la comisión no se devuelve). Para el lago, un cobro
devuelto del todo se ve como `status = REFUNDED` en la vista. Es el mejor ejemplo
de por qué el Lab 1 usa CDC: un cambio sobre un cobro que puede tener días.

Cada paso deja su evento en `webhook_events`, en la misma transacción
(`payment_intent.created`, `.requires_action`, `.payment_failed`, `.succeeded`,
`.canceled`). El worker que los entregaría al comercio no está escrito: llegan al
lago por el CDC. Ver ADR-025.

Dos decisiones que conviene conocer antes de leer el código:

**Una declinación devuelve 200, no un 4xx.** El POS necesita el `response_code`
para decidir qué pinta en pantalla; un error HTTP le quitaría el dato. El 4xx
queda para lo que de verdad está mal en la petición.

**La página del QR la sirve la API, no un bucket.** El QR lleva una URL, el
móvil la abre, y esa página llama a la API. Si vive en otro origen hacen falta
CORS abiertos —había un `allow_origins = ["*"]` en una API de pagos— y un bucket
público servido por HTTP. Con el mismo origen no hace falta ninguna de las dos
cosas. Ver ADR-021.

**La idempotencia son tres transacciones separadas, no una.** La reserva de la
clave se *commitea* antes de ejecutar el cobro, porque si viviera en la misma
transacción ninguna petición concurrente la vería y el candado no serviría de
nada. Si el cobro falla, una tercera transacción libera la clave para que el
reintento legítimo no choque con un candado huérfano.

```bash
# La base
docker run -d --name pg-acq -e POSTGRES_PASSWORD=demo -e POSTGRES_DB=acquiring \
  -p 55433:5432 postgres:16.3-alpine
for f in schema/0*.sql; do
  docker exec -i pg-acq psql -U postgres -d acquiring -q -v ON_ERROR_STOP=1 < "$f"
done

# La API
cd backend
pip install -r requirements.txt
DB_PORT=55433 DB_PASSWORD=demo uvicorn app.main:app --reload --port 8080
```

La página de pago queda en `http://localhost:8080/pay/PI0001`, y se puede abrir
en el móvil apuntando a la IP de la máquina.

Sin `IOT_ENDPOINT` los eventos van al log en vez de a IoT Core, así que el
servicio funciona entero sin AWS. La documentación interactiva queda en
`http://localhost:8080/docs`.

Un cobro completo:

```bash
curl -X POST localhost:8080/v1/intents -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-1' \
  -d '{"intent_reference":"PI0001","terminal_id":"TID00001","amount_minor":1850,"currency_code":"PEN"}'

curl -X POST localhost:8080/v1/intents/PI0001/confirm -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-1-confirm' \
  -d '{"channel":"CARD_PRESENT","card_token":"demo_card_4242","cvv":"123"}'
```

Devuelve el cobro con sus data elements de ISO 8583 y el MDR ya descontado:
sobre 1850 con 299 bps + 30 fijos, `fee_minor: 85` y `net_minor: 1765`. Un CVV
distinto de `123` declina con `response_code: "05"`.

Un pago online con 3DS:

```bash
for f in schema/005_demo_portfolio.sql; do
  docker exec -i pg-acq psql -U postgres -d acquiring -q < "$f"; done
DB_PORT=55433 DB_PASSWORD=demo python ../scripts/provision_demo_keys.py --out /tmp/keys.json
KEY=$(jq -r '."MID000000020007"' /tmp/keys.json)    # TecnoMarket Online

curl -X POST localhost:8080/v1/intents -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"intent_reference":"PIWEB1","amount_minor":129900,"return_url":"http://localhost:3000/gracias"}'

curl -X POST localhost:8080/v1/intents/PIWEB1/confirm -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"channel":"CARD_NOT_PRESENT","payment_method":"pm_card_threeDSecure2Required"}'
# → status REQUIRES_ACTION y next_action.redirect_to_url: ábrela en el navegador
```

Tráfico de toda la cartera, como lo haría la audiencia:

```bash
python ../scripts/generate_traffic.py --api http://localhost:8080 --keys /tmp/keys.json --count 200
```

Los tests son la forma corta de verlo todo:

```bash
pytest tests/test_api.py tests/test_online.py tests/test_traffic.py -v    # desde la raíz, con la base levantada
```

---

## Probar el esquema en local, sin AWS

```bash
docker run -d --name pg-acq -e POSTGRES_PASSWORD=test -e POSTGRES_DB=acq postgres:16
for f in schema/00*.sql; do docker exec -i pg-acq psql -U postgres -d acq -v ON_ERROR_STOP=1 < "$f"; done

# La vista que replica DMS al data lake (Lab 1)
docker exec pg-acq psql -U postgres -d acq \
  -c "SET search_path TO acquiring,public; SELECT * FROM v_transactions_for_ingestion;"

# La vista que consulta el comercio
docker exec pg-acq psql -U postgres -d acq \
  -c "SET search_path TO acquiring,public; SELECT * FROM v_merchant_operations;"

docker rm -f pg-acq
```

Documentación del modelo: [`schema/README.md`](schema/README.md).
