# Renombrar un cliente no tiene procedimiento

- **Detectada:** 2026-10-29, por los evaluadores de agentes e IA y de arquitectura
- **Guía y regla:** arquitectura · ARQ-5, ARQ-6; agentes e IA · IA-2
- **Severidad:** media
- **Estado:** abierta

## Qué es
El slug de un cliente aparece en datos inmutables y en convenciones: `secret_ref` de
las versiones publicadas, la ruta de sus personas de evals (`seeds/tenants/<slug>/`),
la migración de datos `0011` y la regla del registro MCP local. No hay comando ni
migración para renombrarlo.

## Cómo se ha investigado
Al renombrar `logistica_demo` a `logistica` en una base local, la versión publicada de
Logística sigue usando `secret://logistica_demo/registro`. El registro MCP la rechaza
(falla cerrado) y `credential.verify` devuelve `unavailable`: el agente sigue el brief
y manda todos los certificados a revisión manual, sin error visible. Corregirlo exige
publicar una versión nueva por la puerta de evals.

## Por qué importa
Cualquier cambio de nombre comercial de un cliente rompe en silencio integraciones de
sus versiones activas y de las sesiones en curso.

## Opciones
1. Separar identidad estable (UUID) de slug visible en las referencias de secretos y
   personas. Esfuerzo medio.
2. Un comando de renombrado que prepare un borrador con las referencias corregidas y
   recuerde publicarlo por la puerta. Esfuerzo bajo.

## Recomendación
La 2 si los renombrados son raros; la 1 antes de tener muchos clientes.
