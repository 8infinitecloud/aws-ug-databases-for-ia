# Smart Coffee — POS físico y backend de adquirencia

> **DEMO EDUCATIVA — NO PROCESA PAGOS REALES.**
>
> No hay integración con Visa, Mastercard, Yape, Plin ni ningún procesador. No se
> transmiten ni almacenan datos de tarjetas reales: el único instrumento es un
> token ficticio, `demo_card_4242`. Los códigos de autorización llevan prefijo
> `MOCK-`. Ver [Seguridad](#seguridad).

---

## Nuestra posición: adquirente, no comercio

Esto condiciona todo el diseño:

```
Smart Coffee          FinCorp Payments        Red de tarjetas      Emisor
(comercio afiliado)   (NOSOTROS)              (Visa/Mastercard)   (banco del
                                                                   cliente)
     │                       │                       │                │
 vende café           afilia comercios          enruta y         autoriza
 opera el POS         autoriza y captura        liquida entre     contra el
                      liquida al comercio       bancos           saldo
                      cobra MDR
```

Smart Coffee es **uno de nuestros comercios**. Su POS es un terminal que
provisionamos nosotros. Cuando alguien compra un café, el terminal nos pide una
autorización, y ese es el evento de negocio donde empieza la plataforma de datos.

---

## Estructura

| Directorio | Qué |
|---|---|
| `schema/` | Modelo transaccional del adquirente (Aurora PostgreSQL Serverless v2) |
| `backend/` | API de adquirencia sobre Fargate |
| `web/` | Página de pago QR (S3 static website) |
| `firmware/` | POS sobre M5Stack Core2 for AWS IoT Kit (ESP-IDF) |

La infraestructura está en [`../infra/smart-coffee/`](../infra/smart-coffee/).

---

## Estado

| Componente | Estado |
|---|---|
| Schema transaccional | ✅ Implementado y validado contra PostgreSQL 16 |
| Backend de adquirencia | 🔲 Pendiente |
| Terraform (Aurora, Fargate, API Gateway) | 🔲 Pendiente |
| Web de pago QR | 🔲 Pendiente |
| Firmware del POS | 🔲 Pendiente |

El diseño completo está en
[`../docs/architecture-demo-proposal.md`](../docs/architecture-demo-proposal.md).

---

## Seguridad

Lo que este sistema **no** hace:

- No procesa pagos reales ni se conecta a ninguna red de tarjetas
- No almacena PAN. El schema tiene una barrera explícita: `last_four` es `CHAR(4)`
- No usa CVV reales. El del mock es un valor configurable de 3 dígitos
- No hay credenciales bancarias ni datos personales reales

Lo que **sí** hay que proteger, porque la infraestructura es real:

- La contraseña de Aurora va en Secrets Manager, nunca en el código
- El state de Terraform guarda contraseñas en claro y está en `.gitignore`
- La clave privada del POS vive en el elemento seguro ATECC608 y no se puede
  extraer: no hay secreto que versionar

---

## Probar el schema en local

Sin AWS, con Docker:

```bash
docker run -d --name pg-acq -e POSTGRES_PASSWORD=test -e POSTGRES_DB=acq postgres:16
docker exec -i pg-acq psql -U postgres -d acq < schema/001_schema.sql
docker exec -i pg-acq psql -U postgres -d acq < schema/002_seed.sql

# La vista que consume el Lab 1
docker exec pg-acq psql -U postgres -d acq \
  -c "SET search_path TO acquiring,public; SELECT * FROM v_transactions_for_ingestion;"

docker rm -f pg-acq
```

Documentación del modelo: [`schema/README.md`](schema/README.md).
