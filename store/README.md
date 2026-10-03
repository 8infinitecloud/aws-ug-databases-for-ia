# Mercado Andino — la tienda de la demo

> **DEMO EDUCATIVA.** Tienda ficticia, sin marcas reales. No se cobra nada: las
> "tarjetas" son los PaymentMethods de prueba de la API de FinCorp.

Un supermercado online para hacer **compras de verdad durante la demo** y
comparar los canales con datos que no salen del generador de tráfico.

**Es el comercio, no el adquirente.** Corre aparte y cobra a través de la API de
FinCorp con su propia clave, como cualquier tienda con Stripe:

```
navegador ──▶ store/ (Mercado Andino)  ── Bearer sk_demo_… ──▶ API de FinCorp
              ├─ su página                                      (el adquirente)
              └─ su backend: guarda la clave y pone los precios
```

Dos decisiones que no son decoración:

- **La clave secreta nunca llega al navegador.** Por eso hay backend y no solo
  una página: con la clave en el JavaScript, cualquiera podría copiarla y cobrar
  en nombre de la tienda.
- **El total lo calcula el servidor** con los precios de su catálogo. El
  navegador solo dice qué productos y cuántos; un precio que llegara del cliente
  se podría cambiar para pagar un sol por un televisor.

## Arrancar

Con el core desplegado (`scripts/deploy_core.sh`) y las claves creadas:

```bash
python scripts/provision_demo_keys.py --ssm     # incluye la de Mercado Andino
./scripts/store_local.sh                        # http://localhost:8090
```

El script lee la URL de la API de Terraform y la clave de `.demo_keys.json`, y
arranca el contenedor. Para pararlo: `docker rm -f mercado-andino`.

**Solo en local, por decisión.** Las compras online las hace el ponente con la
tienda proyectada; la sala no puede abrirla desde sus móviles (haría falta
publicarla, ver `docs/backlog.md`).

## Los dos canales

| Botón | Canal | Qué pasa |
|---|---|---|
| **Pagar online** | `CARD_NOT_PRESENT` | Se elige una tarjeta de prueba: aprobada, rechazada (`05`), sin fondos (`51`), vencida (`54`) o una que **pide 3-D Secure**, que lleva a la página del banco simulado y vuelve a `/gracias` |
| **Pagar en caja** | `QR` | La tienda abre el cobro en su terminal (`TID00010`) y muestra un **QR grande para proyectar**. Se paga desde el móvil en la página de FinCorp; la tienda espera y avisa |

Todo lo que se compra aquí llega al lago por el CDC del Lab 1 como cualquier otro
pago, con `merchant_name = Mercado Andino`.

## Tests

`tests/test_store.py` conecta la tienda con la API real de FinCorp en memoria y
una base PostgreSQL: el total del servidor frente a precios manipulados, que la
clave no aparece en ninguna respuesta, cada tarjeta de prueba, el 3DS de ida y
vuelta y el QR de la caja.
