# Instrucciones para agentes

Plataforma de cribado conversacional de candidatos con agentes de IA: un núcleo
configurable por cliente, chat web, voz y mensajería para el candidato, y una consola
para el equipo de selección y el FDE. Qué hace: [`docs/producto.md`](docs/producto.md).

## Antes de tocar nada

1. Lee [`GLOSSARY.md`](GLOSSARY.md) y usa sus términos.
2. Lee [`docs/guias/README.md`](docs/guias/README.md) y la guía de cada área que vayas a
   tocar. Sus invariantes no se negocian sin una excepción razonada y aceptada.
3. Para el detalle técnico, [`docs/arquitectura.md`](docs/arquitectura.md) y
   [`docs/convenciones.md`](docs/convenciones.md). Mapa de la documentación:
   [`docs/README.md`](docs/README.md).

## Flujo de trabajo

1. **Alinear.** Si la tarea es ambigua o grande, aclara antes de programar (skills
   `grill-me` o `grill-with-docs`). Los hechos del repositorio los averiguas tú; las
   decisiones son de la persona.
2. **Implementar en vertical**, con pruebas en el nivel adecuado (skill `tdd`,
   [guía de testing](docs/guias/testing.md)). Una tarea cada vez, completa.
3. **Documentar** en el mismo cambio lo que altere comportamiento observable
   ([guía de documentación funcional](docs/guias/documentacion-funcional.md)) y los
   términos nuevos del glosario.
4. **Comprobar**: `make check` en verde. Si tocas la interfaz, revísala en el
   navegador. Si tocas el comportamiento del agente, necesitas evals
   ([guía de agentes e IA](docs/guias/agentes-e-ia.md)).
5. **Evaluar** con el skill `evaluar-cambios`: un evaluador independiente por guía,
   en paralelo. Escribe antes tus notas para los evaluadores, incluidas las
   excepciones deliberadas. Para cada hallazgo, corrige, justifica, registra como
   deuda o rechaza con evidencia. No des el trabajo por terminado con un hallazgo
   bloqueante abierto.
6. **Informar de la deuda.** Si los evaluadores detectan deuda técnica o un problema
   que no recoge ninguna guía, investígalo, regístralo en
   [`docs/deuda-tecnica/`](docs/deuda-tecnica/README.md) y díselo a la persona
   responsable en tu mensaje final, aunque no lo haya preguntado.
7. **Commit** en una rama, con mensaje en español que diga qué y por qué. Nunca
   `git push` sin que te lo pidan.

Si un comando falla dos veces seguidas por algo que no entiendes, o la información
necesaria se contradice, para esa tarea y pregunta en lugar de improvisar.

## Lo que nunca debes hacer

- `git push`, crear repositorios remotos o tocar la configuración global de git.
- Escribir o mostrar secretos, contraseñas, tokens o credenciales en ficheros
  versionados, logs, salidas de comandos o mensajes. No imprimas `.env` ni
  `.runtime-secrets/`; `.env.example` solo lleva valores de ejemplo.
- Usar un perfil de AWS distinto del que indique `AWS_PROFILE` en `.env`. Si una
  llamada falla por credenciales o permisos, para y pregunta; no inicies sesión ni
  cambies la configuración de AWS.
- Llamar a un modelo real (evals, `make record`, pruebas manuales) sin autorización
  de la persona responsable y un tope de gasto. `make test` y la CI nunca llaman a
  modelos ni proveedores reales.
- Enviar mensajes, SMS o llamadas reales, abrir accesos públicos (túneles, ingress)
  o ampliar destinatarios sin autorización explícita para esa prueba.
- Desactivar o debilitar tests, reglas de lint o comprobaciones de tipos para que
  algo pase. `# type: ignore` y `# noqa` solo con un comentario que explique la causa.
- Borrar o sobrescribir trabajo que no es tuyo, o datos de una base local, sin
  confirmarlo; antes de operaciones destructivas sobre la base, haz una copia.
- Introducir código específico de un cliente en la plataforma.

## Idioma

- Código, identificadores y comentarios: en inglés.
- Textos que ven las personas (consola, chat, errores de la API) y documentación: en
  español. El chat del candidato, además, en inglés.
- Excepción: `README.md`, `docs/process-design.md` y `docs/example-conversations.md`
  están en inglés porque son los entregables del case study para una revisión en
  inglés. `docs/diseno-proceso.md` es su versión española; mantén ambas alineadas.

## Experiencia del candidato

El recorrido del candidato es el de un producto terminado, también en móvil: sin
etiquetas de prueba, ensayo o entorno interno, sin proveedores, presupuestos ni topes
técnicos en formularios y mensajes ordinarios. Se mantienen la identificación como
asistente de IA, el consentimiento y el acceso al detalle de privacidad. Detalle en
la [guía de interfaz](docs/guias/interfaz.md).

## Agent skills

Skills en [`.agents/skills/`](.agents/skills/README.md) (Claude Code los ve por
`.claude/skills`). El propio de este repositorio es `evaluar-cambios`; el resto vienen
de Matt Pocock, adaptados a estas guías.

### Issue tracker

Markdown local en `.scratch/` (ignorado por git). Véase `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `GLOSSARY.md` y `docs/adr/` en la raíz, más las guías de `docs/guias/`.
Véase `docs/agents/domain.md`.
