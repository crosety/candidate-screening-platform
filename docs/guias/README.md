# Guías de ingeniería

Estas guías fijan lo que tiene que cumplir cualquier cambio en la plataforma. Son
pocas a propósito: cada una cubre un aspecto transversal y tiene un **evaluador
independiente** que revisa los cambios contra ella antes de dar un trabajo por
terminado.

| Guía | Qué protege |
|---|---|
| [Arquitectura](arquitectura.md) | Capas, puertos y adaptadores, aislamiento por cliente e invariantes del núcleo |
| [Testing](testing.md) | Estrategia de pruebas, dónde va cada test y qué nunca se hace |
| [Seguridad y privacidad](seguridad-y-privacidad.md) | Datos de candidatos, secretos, permisos, canales externos y abuso del agente |
| [Agentes e IA](agentes-e-ia.md) | Reparto entre plataforma y modelo, prompts, evals, coste y trazabilidad |
| [Interfaz](interfaz.md) | Recorrido del candidato, consola de selección y del FDE, idiomas y accesibilidad |
| [Documentación funcional](documentacion-funcional.md) | Que lo documentado describa lo que el código hace hoy |

## Cómo se usan

1. **Antes de cambiar algo**, lee la guía del área que vas a tocar y el
   [glosario](../../GLOSSARY.md).
2. **Antes de dar el trabajo por hecho**, ejecuta el skill `evaluar-cambios`
   (`.agents/skills/evaluar-cambios/`). Lanza un evaluador por cada guía de esta
   carpeta, en paralelo y con contexto limpio, sobre el mismo diff.
3. Si un cambio se aparta de una regla **a propósito**, dilo en las notas para los
   evaluadores: qué regla, por qué y qué lo compensa. El evaluador lo tiene en
   cuenta, y puede aceptar la excepción o explicar por qué no le convence.
4. Lo que devuelven los evaluadores son **avisos razonados**, no un semáforo. Quien
   implementa decide qué corrige, qué justifica y qué queda como deuda. La deuda
   detectada se investiga y se registra en [`docs/deuda-tecnica/`](../deuda-tecnica/)
   y se comunica siempre a la persona responsable.

Añadir una guía añade un evaluador: `evaluar-cambios` descubre todos los `.md` de esta
carpeta salvo este README. Antes de añadir una, comprueba que el tema no cabe en una
existente; preferimos pocas guías bien mantenidas.

## Formato de una guía

Cada guía sigue la misma estructura para que su evaluador pueda citarla:

- **Propósito y alcance**: qué cambios le afectan.
- **Principios**: el criterio de fondo, para los casos que las reglas no prevén.
- **Invariantes** (`XXX-n`): reglas que se cumplen siempre. Saltarse una exige una
  nota explícita y aceptación de la persona responsable.
- **Recomendaciones** (`XXX-Rn`): lo esperable; apartarse es legítimo si se razona.
- **Señales de deuda**: patrones que, aunque cada cambio parezca aceptable, se
  acumulan.
- **Qué revisa el evaluador**: la lista concreta que recorre sobre el diff.

Las reglas se citan por su identificador (`ARQ-3`, `TST-R2`…). Si cambias el sentido
de una regla, cambia también su texto y avisa en el mismo cambio; no reutilices un
identificador para otra cosa.
