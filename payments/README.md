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
| `web/` | Página de pago QR (S3 static website) |
| `firmware/` | Firmware del terminal que provisionamos (M5Stack Core2 for AWS IoT Kit) |

La infraestructura está en [`../infra/payments/`](../infra/payments/).

---

## Estado

| Componente | Estado |
|---|---|
| Schema transaccional | ✅ Validado contra PostgreSQL 16 |
| Interfaz con el comercio (API keys, webhooks) | ✅ Modelado · entrega no implementada |
| Dominio de adquirencia (autorizar, capturar, libro mayor) | 🔄 En curso |
| API HTTP + idempotencia | 🔄 En curso |
| Terraform (Aurora, Fargate, API Gateway) | 🔲 Pendiente |
| Web de pago QR | 🔲 Pendiente |
| Firmware del terminal | 🔲 Pendiente |

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

## Probar en local, sin AWS

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
