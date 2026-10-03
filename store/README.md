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

## El número de tarjeta nunca sale del navegador

El formulario pide número, vencimiento y CVC, como uno de verdad, pero hace lo
que hace Stripe.js: **tokeniza en el navegador**.

```
4242 4242 4242 4242 · 12/34 · 123    ──(en el navegador)──▶   pm_card_visa
                                                                  │
                                         solo esto viaja a la tienda
```

- Solo acepta los **números de prueba públicos** (los que documenta Stripe:
  `4242…`, `5555 5555 5555 4444`, `3782 822463 10005`…). Cualquier otro número se
  rechaza en el navegador, **sin enviar nada a ningún sitio**.
- El backend de la tienda solo recibe el token, y solo acepta tokens de prueba.
  Nunca ve un número ni un CVC: eso es lo que deja a un comercio fuera del
  alcance de PCI, y la razón de ser de la tokenización.
- Los campos llevan `autocomplete="off"`, para que el navegador no ofrezca
  rellenarlos con tarjetas guardadas de verdad.

## Los dos canales

| Botón | Canal | Qué pasa |
|---|---|---|
| **Pagar online** | `CARD_NOT_PRESENT` | Un checkout como el de cualquier tienda: medio de pago (tarjeta; Apple Pay, Google Pay y Yape visibles pero no disponibles), marca (Visa, Mastercard, American Express), número con la marca detectada, vencimiento, CVC y titular. Abajo, **botones que rellenan el formulario** con cada tarjeta de prueba: aprobadas, rechazada (`05`), sin fondos (`51`), vencida (`54`) y una que **pide 3-D Secure**, que lleva a la página del banco simulado y vuelve a `/gracias` |
| **Pagar en caja** | `QR` | La tienda abre el cobro en su terminal (`TID00010`) y muestra un **QR grande para proyectar**. Se paga desde el móvil en la página de FinCorp; la tienda espera y avisa |

Todo lo que se compra aquí llega al lago por el CDC del Lab 1 como cualquier otro
pago, con `merchant_name = Mercado Andino`.

## Tests

`tests/test_store.py` conecta la tienda con la API real de FinCorp en memoria y
una base PostgreSQL: el total del servidor frente a precios manipulados, que la
clave no aparece en ninguna respuesta, que el backend rechaza cualquier cosa que
no sea un token de prueba (también un número de tarjeta), cada tarjeta, el 3DS
de ida y vuelta y el QR de la caja.

El checkout se probó además en un navegador real (Chrome headless), contra la API
en AWS: un número que no es de prueba se rechaza sin ninguna petición de red;
Mastercard aprobada; 3DS ida y vuelta por la página del banco en AWS; sin fondos;
y el QR de la caja. En toda la sesión, ningún número de tarjeta apareció en una
petición.
