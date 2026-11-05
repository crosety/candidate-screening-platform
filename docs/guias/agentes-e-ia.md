# Guía · Agentes e IA

**Propósito.** Que los agentes sean útiles, predecibles en lo que importa y medibles,
sin convertir el producto en un árbol de reglas. **Alcance:** prompts
(`screening/application/prompts.py`), herramientas y capacidades, middleware del
agente, guardián, evaluador, juez, simulador, clasificadores, configuración de
modelos (`adapters/llm`), personas de evals, perfiles y FAQ.

## Principios

1. **Agent-first, determinismo en los bordes.** El modelo conversa, interpreta y
   redacta. La plataforma decide lo que debe ser exacto y auditable: tiempos,
   límites, permisos, canal, idempotencia, validación de tipos y persistencia. Si
   una regla de código intenta hacer el trabajo del modelo (detectar intenciones con
   palabras clave, plantillas de respuesta), probablemente sobra.
2. **El contexto manda.** El agente rinde según lo que ve: brief del cliente, oferta,
   fuentes, resultados de herramientas y nota de la plataforma del turno. Antes de
   añadir una regla, comprueba si falta contexto.
3. **Cada agente tiene un papel.** Conversador, guardián, evaluador, juez, simulador,
   copiloto, señales y asistente del reclutador (`recruiter`, que consulta candidaturas
   dentro de los segmentos de la persona): un `RunContext` con su papel, a quién habla y qué permisos
   tiene. No se mezclan papeles en una misma llamada.
4. **Medir antes de creer.** Un cambio en el comportamiento se demuestra con evals,
   no con una conversación suelta que «sale bien».

## Invariantes

- **IA-1 · El texto al candidato lo escribe el agente.** La plataforma no envía
  respuestas fijas redactadas en código salvo mensajes operativos mínimos (errores,
  confirmaciones de canal) y los textos legales. Nunca se envían notas internas al
  candidato.
- **IA-2 · Las acciones pasan por herramientas con permisos.** El agente solo actúa
  mediante herramientas del catálogo de capacidades concedidas en su `RunContext`;
  los resultados de herramientas son la única fuente para afirmar vacantes, reservas
  o envíos.
- **IA-3 · Salida estructurada validada.** El resultado del cribado se extrae con
  salida estructurada y se valida en la plataforma: tipos y opciones del esquema, y
  cada valor respaldado por una **cita literal** del candidato (`quoted_in`). Sin
  cita, el dato no cuenta.
- **IA-4 · El guardián revisa cada respuesta** antes de enviarla y solo veta
  incumplimientos materiales (datos no respaldados, promesas, sensibles, inyección),
  no estilo. Tras las revisiones permitidas, se deriva a una persona.
- **IA-5 · Versiones congeladas.** Una sesión usa el prompt y la versión de perfil
  con que empezó. Cambiar el comportamiento en producción es publicar una versión
  nueva por la puerta de evals (`ARQ-6`).
- **IA-6 · Evals obligatorias para cambios de comportamiento.** Cambiar prompts,
  herramientas, guardián, evaluador, juez, simulador, esquema del resultado o el
  modelo exige una ejecución de evals de cada oferta abierta. La puerta exige que
  todos los casos superen el umbral, el veredicto esperado y sus comprobaciones en la
  **misma** ejecución, con el hash del arnés vigente.
- **IA-7 · El juez no ve la respuesta esperada.** El veredicto esperado se compara
  por código; el juez evalúa la conversación con la misma evidencia de herramientas
  que tuvo el agente.
- **IA-8 · Todo uso de modelo queda trazado** (`TraceSink`): papel, modelo,
  esfuerzo, tokens, coste en micro-dólares, latencia y motivo de parada.
- **IA-9 · Gasto con autorización.** Llamar a un modelo real fuera de `make test`
  (evals, grabaciones, pruebas manuales) requiere autorización de la persona
  responsable con un tope de gasto, y se registra lo gastado.
- **IA-10 · Señales del candidato solo para el tono.** Idioma y ánimo ajustan cómo se
  habla; nunca entran en el veredicto ni en el resultado.

## Recomendaciones

- **IA-R1.** Escribe prompts como instrucciones a un colega competente: objetivo,
  contexto y límites; pocas reglas, con su motivo. Evita listas de prohibiciones.
- **IA-R2.** Antes de añadir una regla al prompt, prueba a dar el dato que faltaba
  en la nota de la plataforma o en el resultado de una herramienta.
- **IA-R3.** Cuando una eval falle, clasifica la causa antes de tocar nada: agente,
  plataforma, arnés (simulador, juez, evaluador) o persona incoherente.
- **IA-R4.** Usa el modelo y el esfuerzo más baratos que mantengan las evals; los
  papeles auxiliares (señales, guardián) deben ser rápidos y con timeout.
- **IA-R5.** Lo específico de un cliente va en su perfil (brief, instrucciones de
  oferta, FAQ), no en los prompts de la plataforma.
- **IA-R6.** Las personas de evals cubren casos límite reales: abandono, fecha
  imposible, cambio de idioma, inyección, FAQ sin respuesta, frustración.

## Señales de deuda

- Prompts de plataforma que acumulan reglas para casos concretos de un cliente.
- Correcciones con palabras clave o expresiones regulares sobre texto libre.
- Evals que se dejan de ejecutar porque son caras o lentas.
- Divergencia entre lo que ve el agente, el guardián, el evaluador y el juez.
- Costes por conversación que crecen sin que nadie los mire.

## Qué revisa el evaluador

- Respuestas fijas al candidato introducidas en código o notas internas que podrían
  llegar a él.
- Herramientas nuevas sin permiso en el catálogo, sin autonomía o sin traza.
- Cambios de prompts, herramientas, modelos o arnés sin evals ni casetes regrabados.
- Validación determinista que sustituye al juicio del modelo, o juicio del modelo
  donde hace falta una garantía determinista (tiempos, permisos, tipos).
- Lógica de un cliente concreto en los prompts de la plataforma.
- Uso de señales del candidato en decisiones.
