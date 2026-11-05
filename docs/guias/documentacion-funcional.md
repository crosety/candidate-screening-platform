# Guía · Documentación funcional

**Propósito.** Que cualquiera pueda saber qué hace hoy la plataforma, para quién y
con qué límites, sin leer el código, y que eso sea verdad. **Alcance:** cualquier
cambio que altere algo observable por un candidato, el equipo de selección, el FDE,
un sistema integrado o quien opera la instalación; y cualquier cambio en `docs/`,
`GLOSSARY.md` o los README.

El mapa de la documentación está en [`docs/README.md`](../README.md).

## Qué es documentación funcional

Describe **comportamiento y reglas de negocio**: recorridos, estados, decisiones,
permisos, canales, límites, integraciones y operación. La documentación técnica
(cómo está hecho) es útil pero secundaria; el código y sus pruebas ya la sostienen.

| Tipo | Dónde | Ejemplos |
|---|---|---|
| Producto | `docs/producto.md` | Actores, recorridos, estados de una candidatura, publicación |
| Capacidades | `docs/*.md` de cada capacidad | Agenda, reenganche y tono, FAQ, diseño de la conversación |
| Integración | `docs/integracion-ats.md`, `docs/ats/openapi.yaml` | Contrato con sistemas del cliente |
| Operación | `docs/privacidad-operacion.md`, `docs/produccion.md`, `docs/despliegue-y-escalado.md` | Datos, entornos, escalado |
| Vocabulario | `GLOSSARY.md` | Términos del dominio con un solo significado |
| Decisiones | `docs/adr/` | Por qué algo es como es, cuando no es evidente |

## Invariantes

- **DOC-1 · Mismo cambio, misma documentación.** Si un cambio altera comportamiento
  observable, el documento funcional que lo describe se actualiza en el mismo
  cambio. Si no existe ninguno que lo cubra, se crea o se amplía el más cercano y se
  enlaza desde el mapa.
- **DOC-2 · Coherencia con el código.** Lo documentado describe lo que el código
  hace hoy: nombres de estados, límites numéricos, horarios, permisos, canales y
  rutas coinciden con la implementación y con la configuración por defecto.
- **DOC-3 · Estado actual, no diario.** Los documentos describen cómo funciona la
  plataforma. Nada de bitácoras de avance, autorizaciones personales ni referencias a
  hitos internos; la historia está en git y en los registros de decisiones.
- **DOC-4 · Límites explícitos.** Lo que no está implementado, lo que depende de una
  configuración externa o lo que solo se ha verificado en local se dice como tal.
  No se presenta un conector local o un doble como un proveedor real.
- **DOC-5 · Un término, un significado.** Los términos del dominio se usan como los
  define el glosario; si un cambio introduce o redefine un concepto, actualiza
  `GLOSSARY.md`.
- **DOC-6 · Contratos versionados.** Un cambio en la API pública, en webhooks o en el
  contrato con el ATS actualiza su especificación y el cliente generado
  (`make api-client`) en el mismo cambio.
- **DOC-7 · Sin datos privados.** Ni secretos, ni datos de personas reales, ni
  identificadores de cuentas de proveedores en la documentación.

## Recomendaciones

- **DOC-R1.** Escribe para la persona que lo va a usar: qué puede hacer, qué pasa
  después y qué no hace el sistema.
- **DOC-R2.** Documentos cortos por capacidad mejor que uno enorme; el mapa los une.
- **DOC-R3.** Cifras y reglas con su origen («configurable en el perfil», «límite de
  plataforma») para que se sepa quién puede cambiarlas.
- **DOC-R4.** Registra una decisión en `docs/adr/` cuando sea difícil de deshacer,
  sorprendente sin contexto y fruto de una elección real entre alternativas.
- **DOC-R5.** Castellano claro; identificadores de código entre comillas invertidas.

## Señales de deuda

- Capacidades visibles en la interfaz sin ningún documento que las explique.
- Documentos que mencionan pantallas, campos o comandos que ya no existen.
- Cifras repetidas en varios documentos que empiezan a no coincidir.
- Glosario y código usando nombres distintos para lo mismo.

## Qué revisa el evaluador

- Cambios de comportamiento sin su actualización funcional, o documentación que
  promete algo que el diff no hace.
- Contradicciones entre el diff y lo documentado: estados, límites, permisos,
  canales, horarios, nombres de pantallas.
- Términos nuevos o redefinidos sin entrada en el glosario.
- Cambios de API o de contratos de integración sin especificación ni cliente
  regenerado.
- Texto de diario, autorizaciones, datos privados o lenguaje que presente un doble
  como real.
