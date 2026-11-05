# Deuda técnica

Registro de la deuda técnica abierta.
Cada entrada la investiga quien la detecta antes de registrarla, y se comunica a la
persona responsable. Una entrada se cierra cuando se resuelve o se decide aceptarla
de forma permanente, indicando el motivo.

## Índice

| Fecha | Tema | Guía | Severidad | Estado |
|---|---|---|---|---|
| 2026-10-29 | [Recuperación de agenda incierta](2026-10-29-conciliacion-de-agenda.md) | Arquitectura | Media | Abierta |
| 2026-10-29 | [Renombrar un cliente no tiene procedimiento](2026-10-29-renombrar-un-cliente.md) | Arquitectura, agentes e IA | Media | Abierta |
| 2026-10-29 | [Hash del arnés grueso](2026-10-29-hash-del-arnes-grueso.md) | Agentes e IA | Baja | Abierta |

## Plantilla

Fichero `AAAA-MM-DD-<tema>.md`:

```markdown
# <Tema>

- **Detectada:** AAAA-MM-DD, por <evaluador de guía | revisión | incidente>
- **Guía y regla:** <guía> · <ID de regla o «criterio propio»>
- **Severidad:** alta | media | baja
- **Estado:** abierta | en curso | resuelta | aceptada

## Qué es
Descripción concreta con ubicación en el código.

## Cómo se ha investigado
Qué se ha comprobado, con qué comandos o lecturas, y qué alcance tiene.

## Por qué importa
Coste de mantenerla: riesgo, fricción para cambiar, impacto en candidatos o clientes.

## Opciones
Alternativas para abordarla, con esfuerzo aproximado.

## Recomendación
Qué y cuándo, y qué señal haría cambiar la prioridad.
```
