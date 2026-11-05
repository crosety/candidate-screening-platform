# Glosario

Términos del dominio con un único significado. Úsalos igual en código, interfaz,
documentación y conversaciones con agentes. Si necesitas un término que no está,
añádelo aquí en el mismo cambio.

## Clientes y configuración

**Cliente** (`tenant`): empresa que usa la plataforma para cribar. Todos sus datos
están aislados por RLS. _Evitar_: cuenta, organización.

**Perfil** (`ProfileContent`): configuración completa de un cliente: brief, modelo
operativo, fuentes, conexiones, permisos, autonomía, esquema del resultado, rúbrica y
ofertas.

**Borrador**: copia editable del perfil, con número de revisión (`rev`). Guardar no
publica.

**Versión**: instantánea inmutable de un perfil publicado (por ejemplo, `1.6`).

**Publicación**: activación de una versión, con su riesgo, aprobaciones y, si es un
piloto, porcentaje o segmento.

**Brief**: instrucciones del cliente para el agente: el puesto, a quién buscan, encaje,
cómo hablar, seguimiento y qué no hacer.

**Oferta** (`offer`): puesto o conjunto de puestos dentro de un cliente, con su
contexto, instrucciones, campos y criterios de encaje, que compone el brief común.

**Esquema del resultado** (`result_schema`): campos que el cribado tiene que obtener,
con tipo, obligatoriedad y opciones.

**Conexión**: acceso configurado a un sistema del cliente (ATS, locales, agendas,
registro MCP).

**Capacidad**: acción o consulta que un agente puede hacer sobre una conexión
(`openings.find`, `interview.book`…). Se conceden por papel en el perfil.

**Dial de autonomía**: nivel por tipo de decisión que fija si la plataforma solo
propone, aplica con revisión o aplica sola, siempre bajo los topes de plataforma.

**FDE** (Forward Deployed Engineer): persona que configura y despliega la plataforma
para un cliente.

**Fuente**: texto del cliente que lee el agente (manual, FAQ, notas). En modo
`context` va en el prompt; en modo `search` se recupera por búsqueda semántica.

**Conector local**: implementación incluida en la plataforma de un sistema del cliente
(ATS, locales, agendas; drivers `sim_*`) para desarrollo, pruebas y clientes sin API.

**Piloto**: publicación limitada a un porcentaje de sesiones nuevas o a un segmento.

**Copiloto**: asistente que propone mejoras del brief y del modelo operativo al FDE.

**Segmento**: subconjunto de candidaturas (por ejemplo, una zona) que limita lo que ve
una persona del equipo o a quién se aplica un piloto.

## Candidatos y conversaciones

**Candidatura** (`Application`): solicitud de una persona a una oferta.

**Sesión** (`Session`): conversación del agente con una candidatura, ligada a una
versión del perfil. Estados: `active`, `handed_off`, `completed`, `abandoned`.

**Canal**: medio por el que llega o sale un mensaje. Una sesión empieza en `web`,
`whatsapp`, `voice` o `sim` (pruebas del FDE); dentro de ella, los mensajes pueden
llegar también por teléfono (`telephone`) o escribirlos el equipo (`human`). El SMS solo
lleva el enlace personal; no es un canal de conversación.

**Enlace personal**: URL privada y con caducidad para que el candidato retome su sesión.

**Derivación** (`handoff`): paso de la conversación a una persona del equipo; la IA se
detiene hasta que se le devuelve.

**Reenganche**: recordatorios que la plataforma programa cuando el candidato deja de
contestar, y el **abandono** con su **etapa** pendiente si no vuelve.

**Señal del turno**: idioma y ánimo observados en un mensaje, con cita literal. Solo
ajustan el tono; nunca son criterio de selección.

**Aviso de plataforma**: mensaje al candidato que escribe la plataforma con una
plantilla y datos exactos, sin modelo: recordatorio y cambios de entrevista,
derivación automática y respuesta a una petición de llamada. No pasa por el guardián.

**Preferencia de idioma**: elección explícita ES/EN del candidato, guardada en su
sesión para las siguientes respuestas de texto y para los avisos de plataforma. Prevalece sobre el idioma observado;
sin elección ni observaciones, se usa el idioma admitido del navegador. Los avisos
usan el español con cualquier idioma distinto del inglés. No traduce el historial
ni modifica la evaluación.

## Evaluación y decisiones

**Evaluador**: agente que extrae los campos del esquema con cita literal y propone un
**veredicto**: `qualified`, `pool`, `not_qualified` o `review`.

**Bolsa** (`pool`): candidatos que cumplen pero sin oportunidad que encaje hoy;
se les ofrece quedarse para futuras vacantes.

**Encaje**: puntuación explicable de una candidatura frente a los criterios de su
oferta. No es una probabilidad.

**Configuración explícita** (v3): separación de identidad y políticas comunes,
campos y requisitos por oferta, y selección de documentos de la biblioteca.
Los perfiles anteriores conservan su resolución histórica.

**Campos heredados / propios**: la oferta usa los campos comunes actuales o una
copia independiente, respectivamente. Una lista propia vacía no hereda.

**Biblioteca**: documentos compartidos editados en un único lugar, seleccionados
por cada oferta para contexto o búsqueda documental.

**Decisión**: acción que sigue al veredicto (`qualify`, `pool`, `reject`, `review`).
Estados: `proposed`, `applied`, `overridden` (corregida por una persona) o `cancelled`.
Hoy solo `qualify` puede aplicarse sola (dial ≥ 2 y sin alertas); el resto se propone
siempre.

**Guardián**: agente que revisa las respuestas del agente de texto antes de enviarlas
y las veta si incumplen algo material. La voz directa requiere el guardián rápido
Jev activo (`JEV_MODE` distinto de `off`); revisa acciones antes de ejecutarlas,
no cada frase del audio generado. Los controles deterministas de permisos son
independientes. El alcance por canal se describe en [Voz](docs/voz.md); no son
garantías equivalentes.

**Outbox**: cola de efectos externos pendientes, con clave idempotente.

**Exportación de métricas**: entrega en segundo plano de mediciones de modelos a un
proyecto privado de Langfuse. No contiene textos de candidatos. La latencia de una
llamada de texto y la duración de una conversación de voz son medidas diferentes.

## Calidad

**Persona** (de evals): candidato sintético con hechos, estilo, veredicto esperado y
comprobaciones `must` / `must_not`.

**Evals**: ejecución de las personas de una oferta contra un borrador, con simulador,
juez y coste trazado.

**Juez**: agente que puntúa cada conversación de evals según la rúbrica, sin ver el
veredicto esperado.

**Puerta de evals**: condición para publicar: todas las personas de cada oferta
abierta superan el umbral con su veredicto y comprobaciones en una misma ejecución.

**Arnés**: código y prompts que ejecutan las evals (simulador, juez, evaluador); su
hash forma parte de la puerta.

**Simulación**: entorno de pruebas del FDE dentro de la consola; sus registros no
cuentan en la operación.
