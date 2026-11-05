# Plataforma de cribado conversacional

Plataforma para cribar candidatos a puestos de primera línea con agentes de IA. Un
mismo núcleo sirve a muchos clientes: cada uno se configura con un perfil versionado
(brief, ofertas, esquema del resultado, fuentes, conexiones y permisos), sin código
específico.

Vocabulario: [glosario](../GLOSSARY.md). Diseño de la conversación:
[diseño del proceso](diseno-proceso.md).

## Actores

| Actor | Qué hace |
|---|---|
| **Candidato** | Se inscribe en una oferta y conversa con el asistente por chat web, voz en el navegador, llamada o WhatsApp |
| **Equipo de selección** (`recruiter`) | Atiende derivaciones, revisa evaluaciones, confirma o corrige propuestas y sigue la agenda y la analítica, dentro de sus segmentos |
| **FDE** (`fde`, `admin`) | Configura el cliente y sus ofertas, prueba con candidaturas sintéticas, ejecuta evals y publica versiones |
| **Sistemas del cliente** | ATS, locales y vacantes, agendas y registros externos, a través de conexiones |

## Recorrido del candidato

1. **Alta.** Desde el enlace de una oferta (`/apply/<cliente>/<oferta>`) o desde una
   candidatura creada por el ATS. El candidato da su nombre, acepta hablar con un
   asistente de IA y recibe un enlace personal para retomar la conversación.
2. **Conversación.** El asistente se presenta como asistente virtual de la empresa y
   pregunta de uno en uno los datos del esquema de la oferta, sin repetir lo ya
   respondido. Responde en el idioma del candidato (español o inglés, también si
   mezcla) y resuelve dudas con la FAQ del cliente citando la fuente.
3. **Encaje.** Con la zona y el turno, consulta locales y vacantes cercanas
   (`openings_find`) y propone la mejor opción; si no encaja ninguna, ofrece entrar
   en la bolsa.
4. **Resumen y evaluación.** Confirma un resumen con el candidato y pide la
   evaluación. El evaluador extrae los datos con evidencia. La plataforma pide
   aclaración de campos inválidos antes de avanzar automáticamente. Si el resultado
   es revisión humana o ya hay un requisito excluyente confirmado, puede cerrar sin
   más preguntas: los campos inválidos o sin cita se muestran sin valor confirmado,
   con confianza baja y una alerta de requisito por aclarar. La extracción original
   se conserva separada en la auditoría de la sesión. Selección puede contrastarla
   con la conversación; esos campos no se escriben en el ATS, tampoco al confirmar
   después la decisión. No se modifican resultados históricos.
5. **Cierre.** Según el veredicto: entrevista de 15 minutos con el encargado del
   local (reserva, `.ics`, cambio o cancelación), bolsa, revisión humana o
   derivación. Ningún descarte se comunica automáticamente.

En cualquier momento el candidato puede pedir una persona: la conversación pasa al
equipo de selección y la IA se detiene hasta que se le devuelva.

### Veredictos

| Veredicto | Significado | Qué pasa |
|---|---|---|
| `qualified` | Cumple lo imprescindible y hay una oportunidad que encaja | Se ofrece entrevista si el dial permite aplicarla sola; si no, queda propuesta y se explica que el equipo la revisará |
| `pool` | Cumple, pero hoy no hay oportunidad o la incorporación queda fuera de plazo | Se ofrece la bolsa |
| `not_qualified` | No cumple un requisito imprescindible | Propuesta para el equipo; nunca descarte automático |
| `review` | Falta un dato imprescindible o hay dudas | Revisión humana |

La decisión que sigue al veredicto se **propone** o se **aplica** según el dial de
autonomía del cliente, dentro de los topes de la plataforma: hoy solo la de apto puede
aplicarse sola (y nunca con alertas, como un intento de inyección); descartes, bolsa y
revisiones se proponen siempre al equipo.

## Canales

| Canal | Cómo funciona |
|---|---|
| Chat web | Canal principal. Se abre con el enlace personal; ES/EN según `?lang`, preferencia guardada, idioma de la sesión o navegador |
| Voz en el navegador | «Hablar aquí»: misma candidatura e historial; las acciones del agente pasan por la misma validación. Véase [voz](voz.md) |
| Llamada | «Llámame»: el candidato confirma móvil y consentimiento; un intento por solicitud, con límites por móvil y presupuesto reservado. Nunca la inicia el modelo |
| SMS | Aviso de un solo sentido con el enlace personal (por ejemplo, tras una llamada atendida). No es un chat |
| WhatsApp | Alta con `ACEPTO <n>` / `ACCEPT <n>` sobre el menú de ofertas abiertas; misma conversación y recordatorios dentro de la ventana permitida |

En el chat de texto, ES/EN se comunica al agente antes del primer saludo. Al cambiar
el selector, las siguientes respuestas usan la elección; el historial conserva su
texto original. Se distinguen la elección explícita y el idioma observado: un nombre,
una fecha o una cifra no deshacen la elección. El idioma del navegador solo sirve
de alternativa cuando aún no hay elección ni observaciones.

El estado de SMS se consulta al abrir «Mi enlace por SMS» y mientras permanece
abierto y visible. Los errores
y su reintento permanecen dentro de ese panel; no aparecen como un envío fallido
en el chat. «Llámame» recupera inicialmente cualquier llamada en curso y consulta
de nuevo al abrir el panel. Solo mantiene consultas periódicas mientras el panel
está abierto o hay una llamada activa, y ofrece reintentar si no puede comprobarla.
Consultar el estado nunca envía SMS ni solicita una llamada.

Los canales externos solo envían con configuración y consentimiento vigentes.
Las llamadas se pueden solicitar desde una candidatura ordinaria o de prueba a
su móvil español, sin habilitar previamente el número, con consentimiento y los
límites de gasto e intentos existentes. Los SMS siguen limitados a candidaturas
de prueba y móviles invitados; la voz directa del navegador y GPT-Live también
mantienen su alcance de prueba. El detalle de elegibilidad está en
[Twilio](twilio.md) y [voz](voz.md).
Detalle de recordatorios, idioma y tono: [reenganche y tono](reenganche-y-tono.md).

## Selección

- **Bandeja**: derivaciones humanas y decisiones pendientes, con el siguiente paso
  claro. **Tomar conversación**, responder y **Devolver a la IA**.
- **Ofertas**: cada oferta con sus candidaturas, las que necesitan atención y el
  enlace de inscripción si está abierta.
- **Candidaturas**: ficha con **Conversación**, **Perfil** (datos y evidencias) y
  **Evaluación** (encaje con criterios, valoración y decisión). El encaje es un
  cálculo explicable de los criterios de la oferta, no una probabilidad; si faltan
  evidencias, muestra cobertura y pendientes.
- **Agenda**: entrevistas por local, con cambio, cancelación y calendario. Véase
  [agenda](agenda.md).
- **Analítica**: finalización, abandono, derivación, duraciones (mediana y p90),
  embudo, etapa de abandono y desgloses por canal, oferta e idioma. Cada cifra dice
  qué cuenta y sobre qué total. El ánimo observado no es criterio de selección.

Cada persona del equipo ve solo los segmentos que tiene permitidos.

## Configuración del FDE

- **Empresa**: identidad, instrucciones comunes, conexiones, capacidades, canales,
  autonomía y campos reutilizables. Los requisitos del puesto viven en cada oferta.
- **Ofertas**: Puesto y condiciones, Entrevista y evaluación, Documentación y
  herramientas y Probar y publicar. Un mismo editor reúne campos y criterios de
  encaje, con herencia o copia independiente explícita. Guardar solo cambia el
  borrador. Véase [Configuración de ofertas](configuracion-ofertas.md).
- **Biblioteca**: único lugar de edición de documentos compartidos; cada oferta
  selecciona sus fuentes. La FAQ se indexa por pregunta para la búsqueda.
  Véase [FAQ](faq-rag.md). La rúbrica del asistente está separada en
  **Simulación → Calidad del agente**.
- **Simulación**: entorno de pruebas del FDE. Conversaciones con candidaturas
  sintéticas por texto y voz, mensajería, evaluaciones e idiomas. Sus registros no
  cuentan en la operación de selección. Al crear una prueba se elige una oferta
  publicada y abierta; si el cliente solo utiliza la configuración común, se
  introduce la referencia del puesto. La API vuelve a comprobar la oferta antes
  de crear el enlace y exige una publicación al 100 %, igual que el alta pública.
  Una publicación parcial no permite crear pruebas que puedan abrir otra versión.
- **Mejora continua**: propuestas de cambio sobre el borrador con su evidencia y sus
  evals; nada se acepta sin pasar la puerta. Con varias ofertas abiertas, las
  propuestas se preparan desde las sugerencias de Empresa y se evalúan oferta por
  oferta.
- **Trazas técnicas**: detalle de la conversación, llamadas al modelo y capacidades.
  Si Langfuse está configurado, **Abrir en Langfuse** lleva a las métricas de esa
  sesión en otra pestaña, con los permisos propios de Langfuse. No exporta el texto
  de la entrevista; véase [Métricas de modelos](metricas-llm.md).

### Publicar una versión

1. **Cambios** muestra el diff del borrador frente a la versión activa y su riesgo.
2. **Evals** (Simulación → Evaluaciones): cada oferta abierta se evalúa con sus
   personas sintéticas. La puerta
   exige que todas superen el umbral, con el veredicto esperado y todas sus
   comprobaciones, en la misma ejecución y con el arnés vigente.
3. **Validar y publicar** comprueba errores y la puerta de evals y pide las aprobaciones que
   exija el riesgo: segunda persona FDE (nunca el autor) y aprobación del cliente
   cuando cambian conexiones o el esquema del resultado. El cliente no tiene usuarios
   en la consola: su aprobación se confirma fuera de la plataforma y un FDE la marca;
   queda registrado qué FDE la marcó, no la evidencia del cliente.
4. Las conversaciones ya iniciadas conservan su versión. **Versiones** permite volver
   a una anterior creando otra publicación; también hay pilotos por porcentaje o por
   segmento.

La vuelta atrás reactiva una versión que ya estuvo activa, sin repetir evaluaciones
ni aprobaciones. Es distinta de publicar los cambios de un borrador. La versión
inicial creada por el seed es un arranque local, no una publicación acreditada por
evaluaciones.

## Garantías

- Aislamiento entre clientes con Row Level Security en todas las tablas de negocio.
- Cada agente actúa con un contexto de ejecución explícito: cliente, candidatura,
  versión y permisos.
- La pasarela de capacidades comprueba permiso y alcance antes de tocar un sistema
  del cliente; el dial distingue proponer de aplicar.
- En texto, el guardián revisa cada respuesta con fuentes y evidencia de herramientas
  antes de enviarla; si no puede corregirse, deriva a una persona. En voz directa se
  validan las herramientas antes de ejecutarlas, no cada frase hablada; detalle y
  configuración del guardián rápido en [Voz](voz.md).
- El evaluador trabaja aislado: lee la conversación, el perfil y la evidencia de
  herramientas como datos, sin poder ejecutar herramientas.
- Los efectos externos pasan por una outbox con clave idempotente; un envío dudoso no
  se da por entregado.
- Versiones, eventos y mensajes del modelo son de solo añadir.
- Enlaces del candidato con caducidad y revocación; el WebSocket usa tickets de un uso.

Estas garantías son técnicas. No sustituyen la evaluación de cumplimiento normativo
del uso de IA en selección que corresponde a cada despliegue. Datos, retención y
retirada: [privacidad y operación](privacidad-operacion.md).

## Integraciones

- **ATS**: contrato en [integración ATS](integracion-ats.md) y
  [OpenAPI](ats/openapi.yaml).
- **Locales, vacantes y agendas**: conectores nativos sobre los datos que el cliente
  comparte; la instalación local incluye conectores locales con datos sintéticos.
- **Registros externos** (por ejemplo, certificados): servidor MCP.
- **Modelos**: proveedor configurable; detalle en [inferencia](openai-runtime.md).

Despliegue, monitorización y escalado: [despliegue y escalado](despliegue-y-escalado.md)
y [producción](produccion.md).
