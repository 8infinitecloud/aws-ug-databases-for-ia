# Marco de Gestión de Riesgos
## Versión 1.8 | Vigente desde: febrero 2024

**Área:** Gestión de Riesgos  
**Clasificación:** Confidencial Interno

---

## 1. Apetito y Tolerancia al Riesgo

FinCorp define su apetito al riesgo como la disposición a asumir riesgos en la búsqueda de sus objetivos estratégicos, dentro de los límites establecidos por el Directorio.

**Apetito al riesgo por categoría:**

| Categoría | Apetito | Descripción |
|-----------|---------|-------------|
| Riesgo de crédito | Moderado | Mantener mora <3% del portafolio |
| Riesgo operacional | Bajo | Pérdidas operacionales <0.5% de ingresos netos |
| Riesgo de liquidez | Muy bajo | LCR >120% en todo momento |
| Riesgo de cumplimiento | Nulo | Cero tolerancia a violaciones regulatorias |
| Riesgo reputacional | Bajo | Maximizar confianza de clientes e inversores |
| Riesgo tecnológico | Bajo | Disponibilidad de sistemas críticos ≥99.9% |

---

## 2. Proceso de Identificación y Evaluación de Riesgos

### 2.1 Ciclo de Gestión de Riesgos

```
Identificar → Evaluar → Mitigar → Monitorear → Reportar
     ↑                                              │
     └──────────────────────────────────────────────┘
```

### 2.2 Metodología de Evaluación (Matriz 5×5)

**Probabilidad:**
- 1: Muy improbable (<5% en 1 año)
- 2: Improbable (5-20%)
- 3: Posible (20-50%)
- 4: Probable (50-80%)
- 5: Casi certero (>80%)

**Impacto:**
- 1: Insignificante (<USD 10K o impacto operacional mínimo)
- 2: Menor (USD 10K-100K)
- 3: Moderado (USD 100K-1M)
- 4: Mayor (USD 1M-10M)
- 5: Catastrófico (>USD 10M o pérdida de licencia)

**Nivel de Riesgo = Probabilidad × Impacto:**
- 1-5: Bajo (verde)
- 6-12: Medio (amarillo)
- 13-20: Alto (naranja)
- 21-25: Crítico (rojo)

---

## 3. Riesgos Críticos Identificados — 2024

### 3.1 Riesgo de Ciberseguridad

**Riesgo:** Acceso no autorizado a sistemas core por actores externos (ransomware, phishing)  
**Nivel residual:** Alto (3×4=12)  
**Controles existentes:** MFA, EDR, SIEM, backups inmutables, plan de respuesta a incidentes  
**Próxima acción:** Implementar Zero Trust Architecture — plazo Q3 2024

### 3.2 Riesgo de Concentración Crediticia

**Riesgo:** El 35% del portafolio de créditos está en el sector inmobiliario  
**Nivel residual:** Medio (3×3=9)  
**Controles:** Límite de concentración por sector (40% máximo), monitoreo mensual  
**Próxima acción:** Diversificar hacia sector manufactura y agro-exportación

### 3.3 Riesgo Regulatorio

**Riesgo:** Cambios regulatorios en AML/FT que requieran ajustes sistémicos costosos  
**Nivel residual:** Medio (2×4=8)  
**Controles:** Monitoreo regulatorio quincenal, participación en mesas de trabajo con la SBS  
**Próxima acción:** Análisis de impacto de nuevas directrices GAFI — plazo Q2 2024

---

## 4. Tres Líneas de Defensa

**Primera línea:** Las áreas de negocio (comercial, operaciones) son los dueños del riesgo y aplican los controles en su día a día.

**Segunda línea:** Gestión de Riesgos y Compliance supervisan, establecen políticas y alertan cuando se excede el apetito. No aprueban transacciones de negocio.

**Tercera línea:** Auditoría Interna realiza revisiones independientes y objetivas. Reporta directamente al Comité de Auditoría del Directorio.

---

## 5. Business Continuity y Disaster Recovery

### 5.1 Objetivos de Recuperación

| Sistema | RTO (Recovery Time Objective) | RPO (Recovery Point Objective) |
|---------|------------------------------|-------------------------------|
| Core bancario | 4 horas | 0 horas (replicación síncrona) |
| Portal web y app móvil | 2 horas | 1 hora |
| Email corporativo | 8 horas | 4 horas |
| Sistemas de reportes | 24 horas | 24 horas |

### 5.2 Escenarios de Continuidad

- **Falla de datacenter principal:** Conmutación automática a datacenter de respaldo en 30 minutos
- **Pandemia o evento masivo:** Plan de trabajo remoto activo para el 80% de la fuerza laboral
- **Falla de proveedor crítico:** Proveedores alternativos pre-contratados para servicios esenciales

### 5.3 Pruebas de Continuidad

- **Prueba de escritorio (tabletop):** Trimestral
- **Prueba técnica de failover:** Semestral
- **Prueba integral (full DR test):** Anual
