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
riesgo" y la tabla dice `transaction_type = TRANSFER` con
`risk_level = HIGH`, alguien tiene que haber escrito la equivalencia.

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

### 3.1 `transaction_type`

Solo estos tres valores son válidos en la zona curated. El core bancario
histórico registraba algunos en español (`DEPÓSITO`, `RETIRO`,
`TRANSFERENCIA`); el pipeline los normaliza antes de curated.

| Valor | Operación | Riesgo AML predominante |
|-------|-----------|-------------------------|
| `DEPOSIT` | Depósito | Colocación de fondos de origen ilícito. Es la fase más vigilada. |
| `WITHDRAWAL` | Retiro | Extracción en efectivo para romper la trazabilidad |
| `TRANSFER` | Transferencia | Estratificación, sobre todo si es internacional |

### 3.2 `status`

| Valor | Significado | ¿Cuenta para los umbrales? |
|-------|-------------|----------------------------|
| `COMPLETED` | Liquidada | **Sí** |
| `PENDING` | Pendiente de liquidación | Sí, si supera el umbral por sí sola |
| `PROCESSING` | En curso | Sí, igual que `PENDING` |
| `FAILED` | Rechazada por el core | No para umbrales, **sí** como señal: ver 6.3 |

### 3.3 `risk_level` del cliente

Viene de la tabla de clientes y determina el nivel de diligencia según la
Política AML sección 4.1.

| Valor | Diligencia aplicable | Frecuencia de revisión |
|-------|---------------------|------------------------|
| `LOW` | Diligencia simplificada | Cada 36 meses |
| `MEDIUM` | Diligencia estándar (CDD) | Cada 24 meses |
| `HIGH` | Diligencia reforzada (EDD) | Cada 12 meses |

### 3.4 `currency`

La plataforma opera en tres monedas. **Los umbrales son por moneda**, no en un
equivalente único: convertir antes de comparar introduce error de tipo de cambio
en la decisión de reportar.

| Valor | Moneda |
|-------|--------|
| `USD` | Dólares estadounidenses |
| `PEN` | Soles peruanos |
| `EUR` | Euros |

### 3.5 `branch_id`

Identifica la sucursal (`BR001` a `BR020`). Se usa para detectar concentración
anómala: ver sección 6.4.

---

## 4. Umbrales de alerta por moneda

Equivalentes operativos del umbral de USD 10 000 de la Política AML 5.1.

| Operación | `USD` | `PEN` | `EUR` |
|-----------|-------|-------|-------|
| `DEPOSIT` en efectivo, un día | 10 000 | 38 000 | 9 200 |
| `WITHDRAWAL` en efectivo, un día | 10 000 | 38 000 | 9 200 |
| `TRANSFER` internacional | 15 000 | 57 000 | 13 800 |
| Operación única, cualquier tipo (AML 4.2) | 50 000 | 190 000 | 46 000 |

Los equivalentes en `PEN` y `EUR` se revisan trimestralmente. Ante una operación
a menos del 5 % del umbral, aplicar el criterio de fraccionamiento de la sección
6.2.

---

## 5. Matriz de decisión

Qué hacer según el tipo de operación y el nivel de riesgo del cliente:

| `transaction_type` | `risk_level` | Bajo umbral | Sobre umbral |
|--------------------|--------------|-------------|--------------|
| `DEPOSIT` | `LOW` | Sin acción | Alerta automática, revisión en 24 h |
| `DEPOSIT` | `MEDIUM` | Sin acción | Alerta + verificar origen de fondos |
| `DEPOSIT` | `HIGH` | **Registrar en el expediente EDD** | Alerta + escalar al Oficial AML el mismo día |
| `WITHDRAWAL` | `LOW` | Sin acción | Alerta automática |
| `WITHDRAWAL` | `MEDIUM` | Sin acción | Alerta + verificar destino |
| `WITHDRAWAL` | `HIGH` | **Registrar** | **Suspender hasta 72 h** (AML 3.1) y escalar |
| `TRANSFER` | `LOW` | Sin acción | Alerta; verificar país de destino |
| `TRANSFER` | `MEDIUM` | Sin acción | Alerta + justificación comercial documentada |
| `TRANSFER` | `HIGH` | **Registrar y revisar en 48 h** | **Suspender y escalar de inmediato** |

**Regla general:** cualquier operación de un cliente `HIGH` que supere su umbral
se escala al Oficial AML **el mismo día hábil**, sin esperar el SLA de 24 horas.

---

## 6. Patrones que exigen revisión

### 6.1 Cliente con volumen inconsistente con su perfil

Incremento superior al 300 % en el volumen mensual respecto a la media de los
seis meses anteriores (AML 5.1). Se calcula sobre `curated/`, agregando por
`customer_id` y por mes.

### 6.2 Fraccionamiento (*structuring*)

Varias operaciones del mismo `customer_id`, mismo `transaction_type` y misma
`currency`, cada una bajo el umbral pero sumando más del doble, en 5 días
hábiles. Es el patrón más común y el que más se escapa cuando se revisa
operación por operación en lugar de agregando por cliente.

### 6.3 Acumulación de `FAILED`

Tres o más operaciones en `FAILED` del mismo `customer_id` en 48 horas,
seguidas de una `COMPLETED` por importe similar, sugieren tanteo del umbral.
Las `FAILED` no cuentan para los umbrales, pero **sí son señal**.

### 6.4 Concentración por sucursal

Un `branch_id` que concentre más del 25 % del volumen en `USD` de un mes, o que
aparezca en más del 40 % de las alertas del trimestre, se revisa aunque cada
operación individual sea normal.

### 6.5 Cliente con operaciones en tres monedas

Un `customer_id` con operaciones en `USD`, `PEN` y `EUR` en el mismo mes exige
justificación documentada. No es sospechoso por sí solo, pero es infrecuente en
banca personal.

---

## 7. Revisión de la zona `rejected/`

Semanal, a cargo del analista de monitoreo. No es una tarea de calidad de datos:
es una tarea de compliance, porque un registro rechazado es una operación que
existió y que no está siendo vigilada.

| `rejection_reason` | Qué significa | Acción |
|--------------------|---------------|--------|
| `NULL_AMOUNT` | La operación llegó sin importe | Recuperar el importe del core. **No se puede evaluar contra umbrales sin él.** |
| `DUPLICATE_TXN_ID` | Dos operaciones con el mismo identificador | Determinar cuál es válida. Puede ser un doble cargo real al cliente. |
| `INVALID_TYPE` | Tipo no reconocido | Escalar a Tecnología: indica un tipo nuevo sin mapear |
| `INVALID_STATUS` | Estado no reconocido | Igual que el anterior |
| `INVALID_CURRENCY` | Moneda fuera de las tres autorizadas | **Escalar de inmediato**: puede ser una operación no autorizada |
| `NULL_CUST_ID` | Operación sin cliente asociado | Crítico: una operación sin titular no se puede vigilar |

> Un pico en `NULL_AMOUNT` o `DUPLICATE_TXN_ID` suele ser un fallo de
> integración, no un intento de evasión. Pero mientras esas operaciones estén en
> `rejected/`, están fuera del monitoreo, y eso es una brecha que hay que
> documentar y cerrar.

---

## 8. Consultas de referencia

Sobre la tabla `financial_platform.transactions_curated` del catálogo de datos:

**Operaciones sobre umbral de clientes de alto riesgo, por moneda:**
```sql
SELECT t.transaction_id, t.customer_id, t.amount, t.currency,
       t.transaction_type, t.status, t.branch_id
FROM transactions_curated t
WHERE t.transaction_type IN ('WITHDRAWAL', 'TRANSFER')
  AND (   (t.currency = 'USD' AND t.amount >= 10000)
       OR (t.currency = 'PEN' AND t.amount >= 38000)
       OR (t.currency = 'EUR' AND t.amount >=  9200))
ORDER BY t.amount DESC;
```

**Posible fraccionamiento: clientes con varias operaciones bajo umbral:**
```sql
SELECT customer_id, currency, transaction_type,
       COUNT(*) AS operaciones, SUM(amount) AS total
FROM transactions_curated
WHERE amount < 10000 AND currency = 'USD'
GROUP BY customer_id, currency, transaction_type
HAVING COUNT(*) >= 3 AND SUM(amount) >= 20000
ORDER BY total DESC;
```

**Concentración por sucursal:**
```sql
SELECT branch_id, COUNT(*) AS operaciones, SUM(amount) AS volumen_usd
FROM transactions_curated
WHERE currency = 'USD'
GROUP BY branch_id
ORDER BY volumen_usd DESC;
```

---

## 9. Responsabilidades

| Rol | Responsabilidad |
|-----|-----------------|
| Analista de monitoreo | Revisar alertas (SLA 24 h) y la zona `rejected/` semanalmente |
| Oficial de Cumplimiento AML | Decidir sobre operaciones escaladas en 72 h; emitir ROS |
| Asesor de cuenta | Documentar la justificación comercial que pida el analista |
| Tecnología | Mantener los mapeos de `transaction_type` y `status`; resolver los picos de rechazo |

---

## 10. Documentos relacionados

- Política de Prevención de Lavado de Activos (AML) v3.2 — umbrales y ROS
- Procedimientos KYC — clasificación de `risk_level`
- Política de Gestión de Riesgos — apetito de riesgo institucional
- Política de Privacidad de Datos — tratamiento de datos de clientes en el análisis
