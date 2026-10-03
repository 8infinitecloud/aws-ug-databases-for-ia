# Integración de la Cartera Legacy
## Versión 1.0 | Vigente desde: octubre 2026
## Área responsable: Datos y Compliance

---

## 1. Contexto

FinCorp absorbió la cartera de comercios de otro adquirente. Su sistema sigue
funcionando **fuera** de nuestra plataforma, en un servidor propio, y migrarlo es
un proyecto de meses. El negocio no puede esperar ese tiempo para analizar la
historia de esos comercios junto con la nuestra.

La solución adoptada: la historia de la cartera absorbida llega al data lake como
un **volcado** a la zona `raw/legacy/`, sin migrar el sistema. El pipeline de
datos la normaliza, la valida con las mismas reglas que la nuestra, y la une a la
zona `curated/`.

---

## 2. Cómo distinguir una fila legacy

En `transactions`, la columna **`source`** dice de dónde viene cada fila:

| `source` | Origen | Calidad esperada |
|----------|--------|------------------|
| `acquiring` | Nuestro core, replicado con cada cambio | Limpia: la base de datos impide los errores |
| `legacy` | La cartera absorbida, por volcado | **Con errores**: el sistema antiguo no validaba |

Las filas `legacy` tienen además señales propias: `transaction_id` con el formato
antiguo (`ch_TX...`), y `device_serial` vacío porque ese sistema no registraba el
dispositivo.

---

## 3. Los problemas que trae, y cómo se tratan

El sistema antiguo no tenía las restricciones de nuestra base, así que su
historia trae errores que la nuestra no puede tener. El pipeline los trata de dos
formas, según si el dato se puede recuperar:

| Problema | Ejemplo | Tratamiento | La fila |
|----------|---------|-------------|---------|
| Códigos antiguos de canal | `TARJETA`, `CODIGO_QR` | Se normalizan a `CARD_PRESENT`, `QR` | **Se conserva** |
| Estados en español | `CAPTURADA`, `RECHAZADA` | Se normalizan a `CAPTURED`, `FAILED` | **Se conserva** |
| Fechas en formato `DD/MM/YYYY` | `17/03/2025` | Se convierten a `YYYY-MM-DD` | **Se conserva** |
| Importe ausente | `amount` vacío | No se puede inventar | Se aparta: `NULL_AMOUNT` |
| Identificador duplicado | dos filas con el mismo `transaction_id` | No hay forma de saber cuál es la buena | Se apartan las dos: `DUPLICATE_TXN_ID` |
| Libro descuadrado | `net_amount` distinto de `amount` - `fee_amount` | Liquidaría mal al comercio | Se aparta: `LEDGER_MISMATCH` |
| Capturada sin aprobación | `status = CAPTURED` con `response_code` distinto de `00` | Contracargo esperando | Se aparta: `CAPTURED_WITHOUT_AUTH` |

Las filas apartadas **no se borran**: quedan en `rejected/` con su motivo en
`rejection_reason`.

---

## 4. Uso permitido de los datos legacy

| Uso | ¿Permitido? | Condición |
|-----|-------------|-----------|
| Análisis histórico del comercio (volumen, estacionalidad) | Sí | Usar solo `curated/` |
| Evaluación de riesgo al revisar un comercio absorbido | Sí | Considerar también sus filas en `rejected/` |
| Cálculo de umbrales de reporte AML | **Con cautela** | Sumar `curated/` + `rejected/`: ver sección 5 |
| Liquidaciones o cobros | **No** | La cartera absorbida liquida desde su propio sistema |

---

## 5. La brecha de monitoreo

Una operación legacy en `rejected/` **existió**, pero **no está siendo vigilada**:
el monitoreo transaccional trabaja sobre `curated/`. Si se rechazó por
`NULL_AMOUNT`, ni siquiera se sabe cuánto movió.

Por eso, para cualquier comercio absorbido que se esté evaluando por riesgo AML:

1. Contar sus filas en `rejected/` (`source = legacy`), por motivo.
2. Si tiene operaciones rechazadas por `NULL_AMOUNT` o `DUPLICATE_TXN_ID`,
   **pedir el detalle al equipo que operaba el sistema antiguo** antes de cerrar
   la evaluación.
3. Documentar en el expediente del comercio que su historia está incompleta.

Un comercio con muchas operaciones rechazadas no es necesariamente sospechoso: el
sistema antiguo era el descuidado. Pero **no se puede afirmar que esté limpio**
mientras haya operaciones que nadie ha revisado.

---

## 6. Si aparece un rechazado del core

Si en `rejected/` aparece una fila con `source = acquiring`, **no es un problema
de datos**: nuestra base impide esos errores. Es un fallo técnico del pipeline o
del backend, y se escala a Ingeniería el mismo día.

---

## 7. Documentos relacionados

- Procedimiento Operativo de Monitoreo Transaccional — revisión de `rejected/`
- Política AML v3.2 — umbrales y reporte de operaciones sospechosas
- Procedimientos KYC — reevaluación de comercios absorbidos
