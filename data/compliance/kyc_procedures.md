# Procedimientos de Conocimiento del Cliente (KYC)
## Manual Operativo | Versión 2.5 | Vigente desde: marzo 2024

**Área:** Operaciones y Cumplimiento  
**Audiencia:** Asesores de cuenta, Analistas de onboarding, Oficial AML

---

## 1. Introducción al Proceso KYC

El proceso KYC (Know Your Customer) es el conjunto de procedimientos que FinCorp implementa para verificar la identidad de sus clientes, entender la naturaleza de sus actividades económicas y evaluar el riesgo que representan. Un KYC robusto es la primera línea de defensa contra el uso de nuestros servicios para actividades ilícitas.

**Principio fundamental:** No inicies ninguna relación comercial sin completar el KYC correspondiente al nivel de riesgo del cliente.

---

## 2. Proceso KYC para Personas Naturales

### 2.1 Documentación Requerida (todos los clientes)

| Documento | Requisito | Aceptado |
|-----------|-----------|----------|
| Documento de identidad | Original vigente | DNI, Pasaporte, Carné de Extranjería |
| Comprobante de domicilio | No mayor a 3 meses | Recibo de servicios, estado de cuenta bancario, contrato de arrendamiento |
| Declaración de origen de fondos | Firmada por el cliente | Formulario F-001 (adjunto) |
| Declaración de beneficiario final | Cuando aplica | Formulario F-002 |

### 2.2 Documentación Adicional según Perfil Económico

**Dependientes/Asalariados:**
- Boleta de pago de los últimos 3 meses
- Contrato laboral (si el monto supera USD 5,000)

**Independientes/Profesionales:**
- RUC activo (registro no mayor a 30 días)
- Últimas 3 declaraciones juradas de impuesto a la renta
- Estados de cuenta bancarios de los últimos 6 meses

**Empresarios/Dueños de negocio:**
- Documentación de la empresa (ver Sección 3)
- Estados financieros auditados del último ejercicio
- Declaración de beneficiarios finales (UBO)

### 2.3 Verificación de Identidad

**Presencial:** El asesor verifica el original del documento de identidad y retiene copia certificada. Registra número de documento, fecha de vencimiento y nombre completo.

**Digital/Remota:** 
1. El cliente carga foto del documento en la plataforma
2. El sistema de verificación biométrica (Veriff integrado) valida la autenticidad
3. Se realiza video selfie para matching facial
4. El analista revisa y aprueba o rechaza en máximo 24 horas

**Obligatorio en todos los casos:** Consulta de listas restrictivas (OFAC SDN, ONU, UIF Perú) mediante el sistema ListCheck. El resultado queda registrado en el expediente del cliente.

---

## 3. Proceso KYC para Personas Jurídicas (Empresas)

### 3.1 Documentación Corporativa Básica

| Documento | Vigencia Máxima |
|-----------|----------------|
| Ficha RUC de SUNAT | 30 días |
| Escritura de constitución y estatutos | No aplica (documento único) |
| Poderes vigentes del representante legal | 30 días desde inscripción en RRPP |
| Lista de accionistas/socios con % de participación | 30 días |
| Estados financieros auditados (últimos 2 años) | Aplica para montos >USD 50,000 |
| Declaración de beneficiarios finales (UBO) | Renovar anualmente |

### 3.2 Identificación de Beneficiarios Finales (UBO)

**Definición aplicable:** Persona natural con control directo o indirecto superior al 25% del capital o los votos.

**Proceso:**
1. El representante legal completa el Formulario UBO-001
2. Para cada UBO identificado: aplicar proceso KYC de persona natural (Sección 2)
3. Si algún UBO es PEP: escalar automáticamente a EDD (ver Sección 5)
4. Si la estructura supera 3 niveles de control: el Oficial AML debe aprobar el onboarding

**Casos especiales:**
- **Empresas listadas en bolsa:** Exentas de declaración UBO; adjuntar constancia de cotización
- **Empresas de propiedad estatal:** Adjuntar normativa que acredite propiedad del Estado
- **Fondos de inversión:** Identificar al gestor del fondo y a inversores con >10% del fondo

### 3.3 Verificación de la Empresa

- Verificar estado activo en SUNAT y RRPP
- Consultar RUC en lista de contribuyentes no habidos o no hallados
- Verificar que el objeto social sea coherente con la actividad declarada
- Google/open source intelligence (OSINT): búsqueda básica de noticias negativas

---

## 4. Apertura de Cuenta — Flujo Operativo

```
Solicitud del cliente
        │
        ▼
[Paso 1] Recopilación de documentos (Asesor)
   SLA: documentos completos en 48 horas hábiles
        │
        ▼
[Paso 2] Verificación de identidad y autenticidad (Analista)
   SLA: 24 horas hábiles
        │
        ▼
[Paso 3] Screening de listas restrictivas (Sistema ListCheck)
   SLA: automático, 15 minutos
        │
        ▼
[Paso 4] Evaluación de riesgo AML (Analista)
   ¿Bajo/Medio riesgo? → Continuar
   ¿Alto riesgo? → Escalar a Oficial AML (SLA: 48h)
        │
        ▼
[Paso 5] Aprobación y apertura de cuenta
   Autoriza: Asesor (bajo riesgo) / Jefe de Operaciones (medio) / Oficial AML (alto)
        │
        ▼
[Paso 6] Carga en el sistema core (CRM + AML-Monitor)
   SLA: 4 horas hábiles post-aprobación
```

---

## 5. Enhanced Due Diligence (EDD) — Clientes de Alto Riesgo

### 5.1 ¿Cuándo se aplica EDD?

EDD es obligatoria cuando el cliente cumple uno o más de los siguientes criterios:

- Es una Persona Expuesta Políticamente (PEP) o familiar/asociado de PEP
- Proviene de un país en la lista de jurisdicciones de alto riesgo del GAFI
- Opera en sectores de mayor riesgo: casas de cambio, casinos, juegos de azar, comercio de metales preciosos, bienes raíces de alto valor, criptoactivos
- Las transacciones esperadas superan USD 100,000 mensuales
- La estructura corporativa presenta opacidad o complejidad inusual

### 5.2 Procedimiento EDD

1. **Aprobación previa:** El Oficial AML debe aprobar el inicio de la relación antes de abrir la cuenta
2. **Fuentes adicionales de verificación:**
   - Informe de inteligencia de negocio (World-Check, LexisNexis Bridger)
   - Referencia comercial de al menos dos instituciones financieras previas
   - Visita presencial a las instalaciones del negocio (para empresas)
3. **Declaración de origen de patrimonio:** Además del origen de fondos, declaración de cómo se construyó el patrimonio del cliente
4. **Aprobación por Comité:** Casos con riesgo muy alto requieren aprobación del Comité de Cumplimiento
5. **Monitoreo reforzado:** Revisión trimestral de transacciones; alertas con umbral reducido al 50%

### 5.3 PEPs — Consideraciones Especiales

- Los PEPs nunca pueden ser rechazados automáticamente por su condición; deben evaluarse individualmente
- La condición de PEP persiste por 12 meses después de dejar el cargo público
- Los familiares directos (cónyuge, hijos, padres) y asociados cercanos de PEPs son considerados "PEPs indirectos" y requieren EDD
- Los PEP extranjeros se consideran automáticamente de alto riesgo; los PEP nacionales son evaluados según el cargo

---

## 6. Renovación y Actualización del KYC

### 6.1 Eventos que Activan una Revisión Inmediata

- El cliente realiza una transacción que supera 3 veces su volumen habitual
- El sistema AML-Monitor genera una alerta sobre el cliente
- Se detecta un cambio en la información del cliente (domicilio, actividad, representante legal)
- Una fuente externa (periodismo, denuncias, regulador) indica actividad inusual

### 6.2 Actualización Periódica

| Clasificación de Riesgo | Frecuencia de Actualización |
|------------------------|----------------------------|
| Bajo | Cada 3 años |
| Medio | Cada 2 años |
| Alto (EDD) | Cada año |
| PEP | Cada 6 meses |

La actualización requiere: verificar que los documentos sigan vigentes, actualizar la declaración de origen de fondos, re-screening de listas restrictivas.

---

## 7. Casos de Rechazo y Cierre de Cuenta

### 7.1 Causales de Rechazo en Onboarding

- Documentos de identidad falsificados o adulterados
- El cliente se niega a proporcionar información requerida
- Imposibilidad de verificar la identidad del beneficiario final
- El cliente figura en listas restrictivas (OFAC SDN, lista ONU)
- Inconsistencia grave entre el perfil declarado y la actividad esperada

### 7.2 Cierre de Cuenta de Clientes Existentes

Causales: incumplimiento reiterado de actualización de KYC, detección de actividad fraudulenta, orden de autoridad competente, cliente figura en lista restrictiva post-onboarding.

**Proceso de cierre:** Notificación al cliente (30 días de anticipación, salvo orden judicial) → Liquidación de posiciones → Transferencia de saldo → Bloqueo en sistema → Notificación al Oficial AML para evaluar si aplica ROS.

---

## 8. Preguntas Frecuentes del Personal

**P: ¿Puedo abrir una cuenta si el cliente no tiene DNI pero sí tiene pasaporte extranjero?**  
R: Sí, el pasaporte extranjero vigente es suficiente para personas naturales no residentes. Para residentes, se requiere Carné de Extranjería.

**P: ¿Qué hago si el cliente se niega a declarar sus beneficiarios finales?**  
R: No procedes con el onboarding. Debes escalar al Jefe de Operaciones y registrar la negativa en el expediente.

**P: ¿El cliente de bajo riesgo necesita declarar origen de fondos?**  
R: Sí, siempre. El nivel de riesgo determina la profundidad de la verificación, no la obligación de declarar.

**P: ¿Cuánto tiempo guardo los documentos del cliente después de cerrar la cuenta?**  
R: 10 años desde el cierre de la relación comercial.
