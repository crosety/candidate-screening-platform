# Reenganche, señales por turno e idiomas

Criterio común: la plataforma pone los bordes deterministas (cuándo, cuántas veces,
qué canal, qué se guarda) y el agente redacta cada mensaje. Nada de esto interviene
en el veredicto.

## Señales por turno: idioma y ánimo

Cada mensaje del candidato pasa por un clasificador (`SignalClassifier`,
rol `senales`, GPT-6.1 Terra con esfuerzo `medium` y tope de 10 s; un doble por palabras clave en
modo de prueba y en los casetes de Bedrock). Devuelve:

- `language`: idioma predominante ISO 639-1, o vacío si el mensaje no basta
  («15/11», «ok»); `code_switching` si mezcla idiomas.
- `mood`: `neutral`, `positive`, `confused`, `frustrated` o `anxious`, con una
  **cita literal** del mensaje. Sin cita literal, el ánimo se descarta.

Se guarda en `Message.language` y `Message.signal`; `Session.language` conserva el
último idioma observado. El evento `turn_signal` registra idioma, ánimo y ajuste,
nunca la cita. Si el clasificador falla o tarda, se registra `turn_signal_failed`
y el turno sigue sin ajuste.

**Ajuste de tono en vivo.** La señal se añade a la nota de plataforma del turno
como observación («no es criterio de selección»): frustración → reconocer la
molestia, acortar y ofrecer pausa o una persona; confusión → reformular con un
ejemplo y una sola pregunta cerrada; ansiedad → tranquilizar y permitir datos
pendientes. El guardián ve la misma nota. El idioma se comunica como hecho y el
agente respeta los idiomas admitidos por el brief. Dentro de ellos, la elección
explícita ES/EN tiene prioridad sobre el idioma observado en las respuestas de
texto. En la consola, cada mensaje muestra idioma y ánimo como
observación; **Analítica** agrega el ánimo por fase y etapa de abandono.

Idioma antes del primer mensaje: el alta de WhatsApp acepta `ACEPTO n` o
`ACCEPT n` y fija el idioma del saludo; el chat web elige ES/EN por `?lang`,
preferencia guardada, idioma de la sesión o navegador.

El chat envía por separado la elección explícita y la alternativa del navegador
antes de abrir la conversación. La elección se guarda en la sesión, también al
cambiar el selector durante la entrevista; un nombre o un número no la deshacen.
Sin elección explícita se usa el último idioma observado y, si no lo hay, la
alternativa del navegador. Se aplica a las siguientes respuestas y recordatorios
de texto, y a los avisos de plataforma de agenda y derivación automática; no traduce
mensajes anteriores ni cambia el veredicto. La preferencia explícita es distinta de
`Session.language`, que sigue siendo una observación.

Límites: la voz no reclasifica cada turno (el agente de voz sigue el idioma del
brief; el agente remoto está configurado con ASR en español). Las señales son
observaciones de un modelo y pueden equivocarse; por eso solo ajustan el tono.

## Reenganche por silencio

`ReengagementPolicy` es configuración del FDE en el perfil: activado, primer aviso
(120 min), máximo de intentos (≤ 3), horas silenciosas (21–9, nunca más laxas que
el suelo de plataforma 22–8) y abandono (24 h tras el último aviso). El dial de
autonomía `followup` decide si el recordatorio se envía solo (≥ 2) o queda como
propuesta para aprobar (1); con 0 no se recuerda y se aplica solo el abandono.

La política viene **desactivada** por defecto (`enabled: false`) y hoy se edita en el
JSON del perfil, no en la consola. El texto `brief.followup` solo indica al agente
cómo redactar los recordatorios: no programa ninguno. Si el brief describe
recordatorios, activa también la política con valores equivalentes.

Cada minuto el worker encola un único `reengagement_tick` (clave por minuto) que:

1. busca sesiones activas, sin dueño humano y cuyo último mensaje es del agente;
2. calcula el siguiente aviso: a los 120 min del último mensaje, después a la
   mañana siguiente (hora local del candidato) y fuera de horas silenciosas;
3. crea un `Followup` idempotente (`author=platform`) y su trabajo de envío;
4. tras el último aviso y el plazo de abandono, marca la sesión `abandoned` con
   `abandoned_stage`: el campo del esquema (o resumen/entrevista) que el agente
   estaba preguntando, inferido de su última pregunta. El evento
   `followups_exhausted` conserva `{stage, reason: "silence"}`.

Cualquier mensaje del candidato cancela los avisos pendientes; el envío vuelve a
comprobar estado, respuesta nueva y horario antes de redactar.

**Envío por canal.** Una identidad de WhatsApp se envía con su proveedor: Twilio
Sandbox por su cola (ventana de 24 h, lista de participantes y presupuesto propios),
Meta por su adaptador y el teléfono de pruebas del FDE por su reloj. Un candidato solo web
recibe el recordatorio dentro de su conversación y, si tiene la pestaña abierta, en
vivo. El turno de recordatorio no tiene herramientas ni puede decidir: la plataforma
le indica que el momento ya está comprobado y que escriba solo el mensaje.

**Evals.** El simulador responde con un turno estructurado (`reply`, `silence` o `end`)
y solo envía el campo del mensaje, así su razonamiento nunca llega al agente. Con `silence`, el harness ejecuta el mismo
turno de recordatorio que producción, con el reloj adelantado a la hora real de
envío, y el juez lo ve marcado como recordatorio. La persona `ines_silencio` cubre
ese recorrido; su resultado debe comprobarse contra la versión y el arnés evaluados.

**Voz.** El silencio en una llamada lo gestiona el proveedor de voz (corte de turno
y de sesión); terminar la llamada no cierra la candidatura y el reenganche posterior
sigue por el canal de texto vinculado.

La aceptación externa de recordatorios por el Sandbox de Twilio sigue pendiente.
Requiere un participante autorizado que haya escrito en las últimas 24 h y evidencia
de recepción; las pruebas locales de cola y contenido no acreditan entrega.
