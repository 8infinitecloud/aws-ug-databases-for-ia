# Política de Pagos Online
## Versión 1.0 | Vigente desde: octubre 2026
## Documento complementario a la Política AML v3.2 y a la Política de Gestión de Riesgos

---

## 1. Alcance

Aplica a las operaciones del canal **`CARD_NOT_PRESENT`**: pagos que un comercio
afiliado cobra desde su tienda online, sin terminal físico. En `transactions`
se reconocen porque `payment_method = CARD_NOT_PRESENT` y `terminal_id` está
vacío.

El canal online tiene un perfil de riesgo distinto del presencial: no hay tarjeta
física que leer ni un cajero que vea al cliente. Por eso tiene controles propios.

---

## 2. Cómo es un pago online

El comercio no cobra en un solo paso. Abre una **intención de pago** y la confirma
con el medio de pago del comprador. El ciclo de vida, en el campo `status` de la
intención:

| Estado | Qué significa |
|--------|---------------|
| `REQUIRES_PAYMENT_METHOD` | La intención existe y espera un medio de pago. **También es el estado al que vuelve tras un rechazo**: el comprador puede reintentar con otra tarjeta sobre la misma intención |
| `REQUIRES_ACTION` | El banco emisor pide **autenticar al comprador** (3-D Secure) antes de autorizar |
| `SUCCEEDED` | Cobrado |
| `CANCELED` | Abandonado sin cobrar. `cancellation_reason` dice por qué (`abandoned`, `requested_by_customer`, `duplicate`, `fraudulent`) |

Cada intento de autorización deja un cobro en `transactions`, con su
`response_code`. Una intención que se rechazó dos veces y se cobró a la tercera
deja **tres cobros**: dos con `status = FAILED` y uno con `status = CAPTURED`.

---

## 3. Autenticación 3-D Secure

3-D Secure (3DS) es el paso en el que el banco del comprador le pide demostrar
que es él —una clave, una notificación en la app del banco— antes de autorizar.

- Si el emisor la exige, la intención pasa a `REQUIRES_ACTION` y el comprador es
  redirigido a la página de su banco.
- Si **se autentica**, se autoriza y se cobra normalmente.
- Si **no se autentica**, **no se crea ningún cobro**: la autorización ni
  siquiera llegó al emisor. La intención vuelve a `REQUIRES_PAYMENT_METHOD` con
  `last_error_code = AUTH_3DS`.

### 3.1 Por qué importa: quién paga el contracargo

En un pago online sin 3DS, si el tarjetahabiente desconoce la compra, el
contracargo lo paga **el comercio** (y, si el comercio no puede, el adquirente).
En un pago autenticado con 3DS, la responsabilidad pasa **al emisor**: es el
llamado *liability shift*.

Por eso esta política exige 3DS a los comercios online de `risk_level = HIGH`, y
lo recomienda a todos los demás.

---

## 4. Card testing

El **card testing** es el fraude característico del canal online: quien tiene una
lista de tarjetas robadas las prueba con compras pequeñas contra una tienda, para
saber cuáles siguen activas antes de usarlas en compras grandes.

### 4.1 Cómo se reconoce

- Muchas operaciones seguidas contra **el mismo comercio**, en pocos minutos.
- **Importes pequeños** (1 a 5 USD, o el equivalente).
- **La mayoría rechazadas**: `response_code` `05` (no honrar), `51` (fondos
  insuficientes) o `54` (tarjeta vencida), porque casi todas esas tarjetas ya
  están bloqueadas.
- Las pocas aprobadas son las que el atacante buscaba.

Los comercios de **bienes digitales** (`mcc = 5816`) son el blanco preferido:
lo que se compra se puede revender al instante y no hay envío que rastrear.

### 4.2 Qué hace el sistema

El scoring en línea agrupa las operaciones online **por comercio** (no hay
terminal) y marca la ventana cuando la proporción de rechazos supera el 50 % con
al menos 4 operaciones. La alerta aparece en segundos, mucho antes de que los
datos lleguen a `curated/`.

### 4.3 Qué hace el analista

1. Confirmar el patrón en `transactions`: muchas filas `FAILED` del mismo
   `customer_id` con importes pequeños y `payment_method = CARD_NOT_PRESENT`.
2. **Retener la liquidación** de las operaciones aprobadas en esa ventana: son
   las que más probablemente acaben en contracargo.
3. Contactar al comercio: su tienda no tiene controles contra bots (límite de
   intentos, CAPTCHA, 3DS obligatorio).
4. Si el comercio es `HIGH` y el patrón se repite, escalar al Oficial AML.

Un card testing **no** es un caso de lavado: es fraude contra el comercio. Pero
las tarjetas probadas con éxito suelen reaparecer en operaciones de lavado, así
que el caso se documenta en el expediente del comercio.

---

## 5. Umbrales del canal online

Una tienda online suma a todos sus compradores a la vez, así que sus umbrales de
velocidad no son los de un terminal:

| Control | Terminal (`CARD_PRESENT`, `QR`) | Tienda online (`CARD_NOT_PRESENT`) |
|---------|--------------------------------|-------------------------------------|
| Operaciones por minuto | 5 | 30 |
| Importe acumulado por minuto | 500 | 20 000 |
| Proporción de rechazos | 50 % (mínimo 4 operaciones) | **50 % (mínimo 4 operaciones)**: no se relaja |

El ratio de rechazos es el mismo en los dos canales a propósito: es la firma del
card testing, y en el canal online es donde más importa.

---

## 6. Los eventos que recibe el comercio

Cada cambio de una intención genera un evento para el comercio, con el tipo que
lo describe: `payment_intent.created`, `payment_intent.requires_action`,
`payment_intent.payment_failed`, `payment_intent.succeeded` y
`payment_intent.canceled`. El comercio debe confiar en estos eventos y **no** en
lo que le diga el navegador del comprador, que se puede manipular.

---

## 7. Documentos relacionados

- Procedimiento Operativo de Monitoreo Transaccional — matriz de decisión por canal
- Política AML v3.2 — umbrales de reporte
- Política de Gestión de Riesgos — clasificación `risk_level` de comercios
