# Procedimiento Operativo de Monitoreo Transaccional
## Versión 2.1 | Vigente desde: marzo 2026
## Documento complementario a la Política AML v3.2

---

## 1. Propósito

La Política AML v3.2 define **qué** hay que vigilar. Este procedimiento define
**cómo** interpretarlo sobre los datos reales de la plataforma: qué campo mirar,
qué valores son válidos, y qué hacer con cada combinación.

Existe porque el analista de monitoreo no lee políticas mientras revisa alertas:
lee filas de una tabla. Si la política dice "transferencias hacia países de alto
riesgo" y la tabla dice `payment_method = QR` con `risk_level = HIGH`, alguien
tiene que haber escrito la equivalencia.

---

## 2. Origen de los datos

Los datos que revisa el analista provienen de la zona **curated** del data lake,
no del sistema transaccional directamente.

| Zona | Qué contiene | Uso en monitoreo |
|------|--------------|------------------|
| `raw/` | Las transacciones tal como llegaron del core bancario, sin validar | No usar para monitoreo: contiene importes nulos y tipos sin normalizar |
| `curated/` | Transacciones validadas contra las 8 reglas de calidad | **Fuente oficial para monitoreo y reportes** |
| `rejected/` | Registros que no pasaron las reglas, con su motivo en `rejection_reason` | Revisión obligatoria semanal: ver sección 7 |

> **Advertencia de conciliación.** Los totales de `curated/` **no cuadran** con
> los del core bancario, porque los registros rechazados no están ahí. Para
> cualquier reporte regulatorio que exija totales completos, hay que sumar
> `curated/` más `rejected/`. Reportar solo `curated/` subdeclara.

---

## 3. Campos y valores válidos

### 3.1 `payment_method`

El canal por el que entró la operación. Solo estos dos valores son válidos en la
zona curated; el core antiguo escribía `TARJETA` y `CODIGO_QR`, y el pipeline los
normaliza antes.

| Valor | Canal | Riesgo predominante |
|-------|-------|---------------------|
| `CARD_PRESENT` | Tarjeta presente en el terminal | Tarjeta clonada, o comercio colaborando con el fraude |
| `QR` | Pago por código QR desde el móvil del pagador | Suplantación del comercio, y trazabilidad más débil del pagador |

### 3.2 `status`

El ciclo de vida de un cobro visto desde el adquirente.

| Valor | Significado | ¿Cuenta para los umbrales? |
|-------|-------------|----------------------------|
| `CAPTURED` | Cobrada y pendiente de liquidar al comercio | **Sí** |
| `REFUNDED` | Devuelta al tarjetahabiente | Sí, y además ver 6.3 |
| `FAILED` | Rechazada por el emisor | No para umbrales, **sí** como señal: ver 6.4 |
| `REVERSED` | Anulada antes de liquidar | No, pero un volumen alto es señal |

**Códigos heredados.** En la zona `raw/` todavía aparecen los que escribía el core
antiguo. El pipeline los normaliza antes de `curated/`, pero conviene reconocerlos
al revisar un registro rechazado:

| En `raw/` | Equivale a |
|-----------|------------|
| `CAPTURADA`, `captured`, `Captured` | `CAPTURED` |
| `RECHAZADA`, `failed`, `Failed` | `FAILED` |
| `DEVUELTA`, `refunded` | `REFUNDED` |
| `REVERSADA` | `REVERSED` |
| `TARJETA`, `POS`, `CARD` | `CARD_PRESENT` |
| `CODIGO_QR`, `QR_CODE` | `QR` |

### 3.3 `risk_level` del comercio

**Ojo con esto:** el `risk_level` es del **comercio afiliado**, no del
tarjetahabiente. Nosotros afiliamos comercios y hacemos su diligencia debida; al
titular de la tarjeta lo conoce su banco emisor, no nosotros.

Determina el nivel de diligencia según la Política AML sección 4.1.

| Valor | Diligencia aplicable | Frecuencia de revisión | MDR típico |
|-------|---------------------|------------------------|------------|
| `LOW` | Diligencia simplificada | Cada 36 meses | 2.49% |
| `MEDIUM` | Diligencia estándar (CDD) | Cada 24 meses | 2.99% |
| `HIGH` | Diligencia reforzada (EDD) | Cada 12 meses | 3.89% |

El MDR sube con el riesgo: es como el adquirente cubre su exposición a
contracargos y a pérdida por fraude.

### 3.3.1 El `mcc` es una señal de riesgo por sí mismo

El Merchant Category Code (ISO 18245) clasifica el giro del comercio, y algunos
giros son inherentemente más expuestos:

| MCC | Giro | Riesgo base |
|-----|------|-------------|
| `5812`, `5814`, `5411`, `5912` | Restaurantes, comida rápida, supermercados, farmacias | `LOW` |
| `5732`, `5691`, `7011` | Electrónica, ropa, hoteles | `MEDIUM` |
| `4722`, `6051`, `7995` | Agencias de viaje, **casas de cambio**, **apuestas** | `HIGH` |

Un comercio cuyo MCC no cuadra con su actividad real es una señal grave: se llama
*transaction laundering*, y consiste en procesar operaciones de un giro prohibido
bajo el MCC de otro permitido.

### 3.4 `currency`

Liquidamos en tres monedas. **Los umbrales son por moneda**, no en un equivalente
único: convertir antes de comparar introduce error de tipo de cambio en la
decisión de reportar.

| Valor | Moneda |
|-------|--------|
| `USD` | Dólares estadounidenses |
| `PEN` | Soles peruanos |
| `EUR` | Euros |

### 3.4.1 `fee_amount` y `net_amount`

La economía de cada operación, y lo que más pregunta el comercio:

| Campo | Qué es |
|-------|--------|
| `amount` | Lo que pagó el tarjetahabiente |
| `fee_amount` | Nuestra comisión: MDR sobre el importe, más la cuota fija |
| `net_amount` | Lo que se le liquida al comercio: `amount - fee_amount` |

**Si `net_amount` no es exactamente `amount - fee_amount`, el registro está
descuadrado y no debe liquidarse.** El pipeline lo aparta a `rejected/` con motivo
`LEDGER_MISMATCH` precisamente por eso: liquidar un importe equivocado es dinero
real mal pagado. Ver sección 7.

### 3.4.2 `response_code` y `rrn`

| Campo | Qué es |
|-------|--------|
| `response_code` | ISO 8583 DE 39. `00` aprobada; `05` no honrar, `51` fondos insuficientes, `54` vencida, `91` emisor no disponible |
| `rrn` | Retrieval Reference Number (DE 37). **La referencia que se usa para localizar la operación en toda la cadena**: es la que pide el comercio al reclamar y la que aparece en un contracargo |
| `auth_code` | El código que devolvió el emisor al aprobar (DE 38) |

Una operación en `CAPTURED` con `response_code` distinto de `00` es una
contradicción: dice estar cobrada y el emisor dice que no la aprobó. El pipeline la
aparta con motivo `CAPTURED_WITHOUT_AUTH`.

### 3.5 `terminal_id` y `device_serial`

`terminal_id` es el TID (ISO 8583 DE 41): identifica el dispositivo que originó
la operación. `device_serial` es el serial del elemento seguro del terminal, que
lo ata al hardware físico.

Un terminal que concentra actividad anómala se revisa aunque su comercio parezca
normal: puede estar en manos de un tercero. Ver 6.5.

---

## 4. Umbrales de alerta por moneda

Equivalentes operativos del umbral de USD 10 000 de la Política AML 5.1.

| Situación | `PEN` | `USD` | `EUR` |
|-----------|-------|-------|-------|
| Operación única `CARD_PRESENT` | 38 000 | 10 000 | 9 200 |
| Operación única `QR` | 19 000 | 5 000 | 4 600 |
| Acumulado diario de un mismo comercio | 190 000 | 50 000 | 46 000 |
| Acumulado diario de un mismo `terminal_id` | 76 000 | 20 000 | 18 400 |

El umbral del canal `QR` es **la mitad** que el de tarjeta presente: la
trazabilidad del pagador es más débil, así que el mismo importe merece más
atención.

Los equivalentes en `PEN` y `EUR` se revisan trimestralmente. Ante una operación
a menos del 5 % del umbral, aplicar el criterio de fraccionamiento de 6.2.

## 5. Matriz de decisión

Qué hacer según el canal y el nivel de riesgo del **comercio**:

| `payment_method` | `risk_level` | Bajo umbral | Sobre umbral |
|------------------|--------------|-------------|--------------|
| `CARD_PRESENT` | `LOW` | Sin acción | Alerta automática, revisión en 24 h |
| `CARD_PRESENT` | `MEDIUM` | Sin acción | Alerta + verificar actividad del comercio |
| `CARD_PRESENT` | `HIGH` | **Registrar en el expediente EDD** | Alerta + escalar al Oficial AML el mismo día |
| `QR` | `LOW` | Sin acción | Alerta automática |
| `QR` | `MEDIUM` | Sin acción | Alerta + verificar el `terminal_id` de origen |
| `QR` | `HIGH` | **Registrar y revisar en 48 h** | **Retener la liquidación** y escalar de inmediato |

**Regla general:** cualquier operación de un comercio `HIGH` que supere su umbral
se escala al Oficial AML **el mismo día hábil**, sin esperar el SLA de 24 horas.

> **Retener la liquidación, no la operación.** Ésta es la diferencia clave con un
> banco: nosotros no podemos «suspender» el pago —ya se autorizó contra el emisor
> y el tarjetahabiente ya tiene su café—. Lo que sí podemos es **no liquidar al
> comercio** hasta aclararlo. Es la palanca real de un adquirente.

## 6. Patrones que exigen revisión

### 6.1 Comercio con volumen inconsistente con su perfil

Incremento superior al 300 % en el volumen mensual respecto a la media de los seis
meses anteriores (AML 5.1). Se calcula sobre `curated/`, agregando por
`customer_id` y por mes.

En un adquirente esto tiene una lectura adicional: un comercio que de pronto
procesa cinco veces más puede estar prestando su MID a un tercero, lo que se
conoce como *factoring* y está prohibido por las reglas de marca.

### 6.2 Fraccionamiento (*structuring*)

Varias operaciones del mismo `customer_id`, mismo `payment_method` y misma
`currency`, cada una bajo el umbral pero sumando más del doble, en 5 días
hábiles. Es el patrón más común y el que más se escapa cuando se revisa operación
por operación en lugar de agregando por comercio.

### 6.3 Ratio de devoluciones anómalo

Más del 5 % de las operaciones de un comercio en `REFUNDED` dentro del mes. Las
devoluciones son el vehículo clásico para mover dinero: se cobra a una tarjeta y
se devuelve a otra.

Un ratio alto también es motivo de suspensión por las reglas de marca,
independientemente del AML.

### 6.4 Acumulación de `FAILED`

Tres o más operaciones en `FAILED` del mismo `customer_id` en 48 horas, seguidas
de una `CAPTURED` por importe similar, sugieren tanteo del límite de la tarjeta.
Las `FAILED` no cuentan para los umbrales, pero **sí son señal**.

### 6.5 Concentración por terminal

Un `terminal_id` que concentre más del 25 % del volumen en `PEN` de su comercio en
un mes, o que aparezca en más del 40 % de las alertas del trimestre, se revisa
aunque cada operación individual sea normal. Puede estar fuera del local
declarado.

### 6.6 MCC que no cuadra con la actividad

Un comercio con MCC `5812` (restaurante) cuyo ticket medio es de PEN 3 000 no está
vendiendo almuerzos. Es el indicio de *transaction laundering* descrito en 3.3.1,
y se escala directamente al Oficial AML.

## 7. Revisión de la zona `rejected/`

Semanal, a cargo del analista de monitoreo. No es una tarea de calidad de datos:
es una tarea de compliance, porque un registro rechazado es una operación que
existió y que no está siendo vigilada.

| `rejection_reason` | Qué significa | Acción |
|--------------------|---------------|--------|
| `NULL_AMOUNT` | La operación llegó sin importe | Recuperar el importe del core. **No se puede evaluar contra umbrales sin él.** |
| `DUPLICATE_TXN_ID` | Dos operaciones con el mismo identificador | Determinar cuál es válida. Puede ser un **doble cobro real** al tarjetahabiente, y eso acaba en contracargo. |
| **`LEDGER_MISMATCH`** | `net_amount` ≠ `amount - fee_amount` | **No liquidar.** Recalcular la comisión con el MDR vigente del comercio. Si se liquidó, hay un ajuste pendiente en ambos sentidos. |
| **`CAPTURED_WITHOUT_AUTH`** | En `CAPTURED` con `response_code` distinto de `00` | **Retener la liquidación y escalar a Riesgos.** Se cobró sin aprobación del emisor: es un contracargo casi seguro. |
| `INVALID_METHOD` | Canal no reconocido | Escalar a Tecnología: indica un canal nuevo sin mapear |
| `INVALID_STATUS` | Estado no reconocido | Igual que el anterior |
| `INVALID_CURRENCY` | Moneda fuera de las tres autorizadas | **Escalar de inmediato**: puede ser una operación no autorizada |
| `NULL_CUST_ID` | Operación sin comercio asociado | Crítico: una operación sin comercio no se puede liquidar ni vigilar |

> **Los dos motivos en negrita son los que cuestan dinero.** Los demás son datos
> incompletos; `LEDGER_MISMATCH` y `CAPTURED_WITHOUT_AUTH` son operaciones que, si
> se liquidan, pagan mal al comercio o generan un contracargo. Tienen prioridad
> sobre el resto de la revisión semanal.

> Un pico en `NULL_AMOUNT` o `DUPLICATE_TXN_ID` suele ser un fallo de
> integración, no un intento de evasión. Pero mientras esas operaciones estén en
> `rejected/`, están fuera del monitoreo **y fuera de la liquidación**, y eso es
> una brecha que hay que documentar y cerrar.

---

## 8. Consultas de referencia

Sobre la tabla `financial_platform.transactions_curated` del catálogo de datos.

**Operaciones sobre umbral de comercios de alto riesgo:**
```sql
SELECT t.transaction_id, t.customer_id, t.merchant_name, t.mcc,
       t.terminal_id, t.amount, t.currency, t.payment_method, t.rrn
FROM transactions_curated t
WHERE t.risk_level = 'HIGH'
  AND t.status = 'CAPTURED'
  AND (   (t.currency = 'PEN' AND t.amount >= 38000)
       OR (t.currency = 'USD' AND t.amount >= 10000)
       OR (t.currency = 'EUR' AND t.amount >=  9200))
ORDER BY t.amount DESC;
```

**Posible fraccionamiento: comercios con varias operaciones bajo umbral:**
```sql
SELECT customer_id, merchant_name, currency, payment_method,
       COUNT(*) AS operaciones, SUM(amount) AS total
FROM transactions_curated
WHERE amount < 38000 AND currency = 'PEN' AND status = 'CAPTURED'
GROUP BY customer_id, merchant_name, currency, payment_method
HAVING COUNT(*) >= 3 AND SUM(amount) >= 76000
ORDER BY total DESC;
```

**Ratio de devoluciones por comercio (6.3):**
```sql
SELECT customer_id, merchant_name,
       COUNT(*) AS total,
       SUM(CASE WHEN status = 'REFUNDED' THEN 1 ELSE 0 END) AS devueltas,
       ROUND(100.0 * SUM(CASE WHEN status = 'REFUNDED' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct
FROM transactions_curated
GROUP BY customer_id, merchant_name
HAVING COUNT(*) >= 20
   AND 100.0 * SUM(CASE WHEN status = 'REFUNDED' THEN 1 ELSE 0 END) / COUNT(*) > 5
ORDER BY pct DESC;
```

**MCC que no cuadra con el ticket medio (6.6):**
```sql
SELECT customer_id, merchant_name, mcc,
       COUNT(*) AS operaciones,
       ROUND(AVG(amount), 2) AS ticket_medio
FROM transactions_curated
WHERE mcc IN ('5812', '5814') AND currency = 'PEN' AND status = 'CAPTURED'
GROUP BY customer_id, merchant_name, mcc
HAVING AVG(amount) > 500
ORDER BY ticket_medio DESC;
```

**Comisiones cobradas y liquidado a comercios, por mes:**
```sql
SELECT date_trunc('month', transaction_date) AS mes,
       COUNT(*)          AS operaciones,
       SUM(amount)       AS bruto,
       SUM(fee_amount)   AS comisiones,
       SUM(net_amount)   AS liquidado
FROM transactions_curated
WHERE status = 'CAPTURED'
GROUP BY 1 ORDER BY 1;
```

## 9. Responsabilidades

| Rol | Responsabilidad |
|-----|-----------------|
| Analista de monitoreo | Revisar alertas (SLA 24 h) y la zona `rejected/` semanalmente |
| Oficial de Cumplimiento AML | Decidir sobre operaciones escaladas en 72 h; emitir ROS |
| Ejecutivo de afiliación | Documentar la justificación comercial del comercio; mantener su expediente KYC |
| Riesgos | Decidir retención de liquidación y suspensión de comercios |
| Tecnología | Mantener los mapeos de `payment_method` y `status`; resolver los picos de rechazo |

---

## 10. Documentos relacionados

- Política de Prevención de Lavado de Activos (AML) v3.2 — umbrales y ROS
- Procedimientos KYC — clasificación de `risk_level`
- Política de Gestión de Riesgos — apetito de riesgo institucional
- Política de Privacidad de Datos — tratamiento de datos de clientes en el análisis
