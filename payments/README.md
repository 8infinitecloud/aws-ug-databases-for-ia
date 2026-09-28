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
| `schema/` | Modelo transaccional del adquirente (Aurora PostgreSQL Serverless v2) |
| `backend/` | API de adquirencia sobre Fargate |
| `web/` | Página de pago QR, servida por la propia API en `/pay/{ref}` |
| `firmware/` | Firmware del terminal (M5Stack Core2 for AWS IoT Kit), compilación local |

La infraestructura está en [`../infra/payments/`](../infra/payments/).

---

## Estado

| Componente | Estado |
|---|---|
| Schema transaccional | ✅ Validado contra PostgreSQL 16 |
| Interfaz con el comercio (API keys, webhooks) | ✅ Modelado · entrega no implementada |
| Dominio de adquirencia (autorizar, capturar, libro mayor) | ✅ Probado contra PostgreSQL real |
| API HTTP + idempotencia | ✅ 19 tests de integración · imagen construida |
| Terraform (Aurora, Fargate, API Gateway) | ✅ `plan` verificado · **no desplegado** |
| Web de pago QR | ✅ Servida en `/pay/{ref}` · mismo origen, sin CORS |
| Firmware del terminal | ✅ Compila (1,0 MB) · **sin grabar en el dispositivo** |

Falta validar dos cosas contra hardware real: un despliegue en AWS y una grabada
en el Core2. Todo lo demás se ejecuta y se prueba en local.

Diseño completo:
[`../docs/architecture-demo-proposal.md`](../docs/architecture-demo-proposal.md).

---

## Seguridad

Lo que este sistema **no** hace:

- No procesa pagos reales ni se conecta a ninguna red de tarjetas
- No almacena PAN. El schema tiene una barrera explícita: `last_four` es `CHAR(4)`
- No usa CVV reales. El del mock es un valor configurable por entorno
- No guarda claves de API en claro, solo su hash SHA-256

Lo que **sí** hay que proteger, porque la infraestructura es real:

- La contraseña de Aurora va en Secrets Manager, nunca en el código
- El state de Terraform guarda contraseñas en claro y está en `.gitignore`
- La clave privada del terminal vive en el elemento seguro ATECC608 y no se puede
  extraer: no hay secreto que versionar

---

## La API

Tres rutas, que son las tres cosas que pasan en un cobro:

| | Quién la llama | Qué hace |
|---|---|---|
| `POST /v1/intents` | el POS | cierra el carrito y crea la intención |
| `GET /v1/intents/{ref}` | la web del QR | lee importe y comercio antes de confirmar |
| `POST /v1/intents/{ref}/confirm` | POS o web | autoriza, captura y asienta en el libro mayor |
| `GET /pay/{ref}` | el móvil que escanea | la página de pago del QR |

Más `GET /health`, que es lo que consulta el health check del ALB.

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

Los tests son la forma corta de verlo todo:

```bash
pytest tests/test_api.py -v    # desde la raíz del repo, con la base levantada
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
