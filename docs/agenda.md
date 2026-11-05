# Agenda de entrevistas

La agenda funciona con un **conector de calendario local** (una agenda por local,
`driver: sim_calendar`) que guarda los huecos en la propia plataforma. Ningún calendario
externo está conectado ni validado; la sección 3 describe el conector para uno real.

## 1. Cómo funciona hoy

### Piezas

| Pieza | Dónde | Qué hace |
|---|---|---|
| Reglas puras | `screening/domain/interviews.py` | Confirmación, hora del recordatorio, horario de silencio, claves de idempotencia e iCalendar (RFC 5545). |
| Servicio | `screening/application/interviews.py` (`InterviewService`) | Reserva, cambio y cancelación; registro de la entrevista; etapa del ATS; recordatorio; avisos; `.ics`. |
| Registro | `conversations.InterviewBooking` (migración `0017`) | Una fila por entrevista con RLS: hueco, local, zona horaria, estado, `sequence`, recordatorio. Una sola entrevista activa por candidatura (restricción única parcial). |
| Calendario local | `web/simulated/connectors.py` + `simulated.CalendarOperation` | `list_slots`, `book_slot`, `reschedule_booking`, `cancel_booking`, con libro de idempotencia. |
| Herramientas | `interview_slots`, `interview_book`, `interview_reschedule`, `interview_cancel` | Todas bajo la capacidad `interview.book` (dial `schedule`). La voz usa la misma lista (`tool_specs`). |
| API | `web/conversations/interview_api.py` | Chat: `POST /chat/interview`, `/chat/interview/manage`, `/chat/interview/ics` (token en el cuerpo). Equipo: `GET /tenants/{id}/interviews`, `GET …/sessions/{id}/interview`, `GET …/interviews/{id}/slots`, `POST …/interviews/{id}`, `GET …/interviews/{id}/ics`. |
| Consola | Página «Agenda» (Selección) y tarjeta en la ficha de la candidatura | Lista por local y día; cambiar, cancelar y descargar el `.ics`. |
| Chat | Tarjeta «Tu entrevista» | Fecha en la zona de la persona, local, dirección, «Añadir al calendario» y «Cambiar o cancelar». |

### Contrato de las herramientas

| Herramienta | Entrada | Salida principal |
|---|---|---|
| `interview_book` | `slot_id` | `status` (`booked`, `taken`, `expired`, `already_booked`, `not_allowed`, `unavailable`), `booking_id`, `confirmation`, `reminder`. |
| `interview_reschedule` | `slot_id` (nuevo hueco) | `status` (`rescheduled`, `taken`, `expired`, `same_slot`, `not_found`, `not_allowed`, `unavailable`) y la misma `confirmation`. |
| `interview_cancel` | ninguna | `status` (`cancelled`, `not_allowed`, `unavailable`) y `cancelled.when`. |

`confirmation` lleva lo que el agente debe confirmar: `when` (fecha y hora en la zona de la
persona candidata), `timezone_label`, `site_name`, `address` (si el local la tiene),
`duration_minutes` (15), `with`, `how_to_change`, `site_local_time` cuando la zona del local
es otra, y `must_confirm`, la instrucción literal.

Las herramientas de cambio no aceptan identificadores de reserva: siempre actúan sobre la
entrevista activa de la candidatura del `RunContext`. El calendario comprueba además que
el hueco pertenece a esa candidatura (`not_owner`).

### Guardas (iguales que `interview_book`)

1. Capacidad `interview.book` concedida y dial `schedule` > 0.
2. ATS con permiso de escritura.
3. Sesión activa y, para reservar, una decisión `qualify` aplicada; para cambiar o cancelar,
   una entrevista activa propia.
4. En voz, cada llamada pasa antes por el guardián de herramientas (Jev y su respaldo), que
   exige petición explícita: hueco ofrecido y elegido para reservar o cambiar; petición y
   confirmación para cancelar.

Al reservar, la conversación pasa a `completed` (como antes). Para cambiar o cancelar, la
persona pulsa «Cambiar o cancelar» en el chat: la conversación vuelve a `active`
(evento `interview_change_requested`) y se lo pide al asistente. Tras un cambio vuelve a
`completed`; tras cancelar sigue activa para poder ofrecer otro hueco.

### Idempotencia y carreras

- Clave por operación: `sha256(sesión, reserva, acción, sequence, hueco)`; para la primera
  reserva, `sesión + "new" + hueco`.
- El calendario guarda la clave y su resultado. Un reintento tras un tiempo de espera
  ambiguo devuelve el mismo resultado (`replayed: true`) en vez de actuar dos veces.
- La plataforma aplica el cambio solo si `sequence` sigue siendo el esperado; si no, devuelve
  la fila actual.
- El calendario serializa todas las escrituras de un cliente con un bloqueo de fila: dos
  personas no pueden quedarse el mismo hueco y nadie puede tener dos. El cambio libera el
  hueco anterior y toma el nuevo en la misma transacción.

### Recordatorio

- Tarea `interview_reminder` (una por versión de la entrevista) programada:
  1. 24 h antes de la entrevista;
  2. si cae entre las 21:00 y las 09:00 de la **zona de la persona candidata**, se adelanta a
     las 20:30 de la tarde anterior;
  3. si ya no da tiempo, 2 h antes con la misma regla;
  4. si tampoco da tiempo, no hay recordatorio.
- Cambiar la entrevista cancela la tarea anterior y crea otra; cancelarla la cancela.
- Al ejecutarse comprueba que la versión (`sequence`) sigue vigente, que no faltan menos de
  30 minutos y que no es horario de silencio (si lo es, se aplaza).
- El texto es fijo, sin modelo, y sigue la preferencia de idioma de la sesión (véase el
  [glosario](../GLOSSARY.md)): inglés si es `en`, español en cualquier otro caso: «Recordatorio de {cliente}: tienes
  una entrevista de 15 minutos el {fecha} ({zona}) en {local}. Para cambiarla o
  cancelarla…». Igual el aviso de cambio o cancelación hecho por selección. Es un
  aviso de plataforma: plantilla con datos exactos, no texto del agente, y no pasa por
  el guardián.
- Se publica en la conversación web de la persona por el mismo camino que los
  recordatorios web (mensaje en la transcripción y envío en vivo a la pestaña abierta),
  con clave idempotente. **Pendiente:** en WhatsApp haría falta una plantilla aprobada de
  recordatorio; hoy no se envía nada fuera de la conversación web.

### Archivo `.ics`

- `UID` estable por entrevista (`{id}@agenda.orbio`); `SEQUENCE` sube en cada cambio o
  cancelación.
- `METHOD:PUBLISH` y `STATUS:CONFIRMED` mientras está activa; `METHOD:CANCEL` y
  `STATUS:CANCELLED` al cancelarla, con el mismo `UID`.
- `DTSTART`/`DTEND` con `TZID` del local y su `VTIMEZONE` calculado con las transiciones
  reales (horario de verano en España; México sin cambio horario). `DTSTAMP` en UTC.
- La persona candidata lo descarga por `POST` con el token en el cuerpo; el chat lo
  convierte en un `Blob`. El token nunca va en una URL.

### Consola

«Agenda» lista por local y día las próximas entrevistas (o también las pasadas), con
estado (reservada, cambiada, cancelada), recordatorio y acciones. Los permisos y el
alcance por segmentos son los de las demás pantallas de selección, con RLS. Cada cambio deja
un `AuditEvent` (`interview.cancel` / `interview.reschedule`), un evento en la conversación,
la etapa en el ATS por la bandeja de salida y un aviso en la conversación de la persona.

## 2. Qué falta para usarlo con un calendario real

- Volver a provisionar el agente de voz remoto: cambia su lista de herramientas.
- Plantilla de WhatsApp aprobada para el recordatorio fuera de la ventana de 24 h.
- Direcciones reales de los locales (`Site.address`; la columna `address` del CSV es
  opcional y hoy está vacía).

## 3. Diseño de un conector real (Google Calendar / Microsoft Graph)

El puerto no cambia: el conector expone `list_slots`, `book_slot`, `reschedule_booking` y
`cancel_booking` con la misma entrada y salida, incluida `idempotency_key`.

### Autenticación

- **Google:** cuenta de servicio con delegación de dominio limitada a los calendarios de los
  locales (`calendar.events`, `calendar.freebusy`), o OAuth de una cuenta técnica del cliente.
  El secreto va en `secret_ref` (`secret://cliente/agendas`), nunca en el perfil.
- **Microsoft Graph:** aplicación con `Calendars.ReadWrite` restringida con una
  *application access policy* a los buzones de los locales; credencial por certificado.
- Un calendario por local, configurado en `settings` de la conexión (mapa
  `site_id → calendar_id`/buzón).

### Disponibilidad

- Huecos = horario de entrevistas del local (configuración) menos ocupado:
  Google `freeBusy.query`, Graph `getSchedule` o `findMeetingTimes`.
- Caché corta (≤ 60 s) y comprobación de nuevo justo antes de escribir: la disponibilidad
  devuelta al agente es orientativa.

### Creación, cambio y cancelación

- **Google:** `events.insert` con `id` derivado de la clave de idempotencia (base32hex): un
  reintento devuelve 409 y se lee el evento existente. Cambio con `events.patch` sobre el
  mismo evento; cancelación con `events.delete` o `status: cancelled`.
- **Graph:** `POST /users/{buzón}/events` con `transactionId` = clave de idempotencia; cambio
  con `PATCH`; cancelación con `POST …/cancel` (envía la cancelación a los asistentes).
- `iCalUID` del evento = el `UID` de la plataforma, para que el `.ics` y el evento coincidan.
- Antes de escribir, volver a consultar ocupación del hueco (o crear el evento como
  provisional y confirmar), porque los calendarios no tienen reserva atómica: si aparece
  un conflicto, deshacer y devolver `taken`.
- Tiempo de espera ambiguo: no reintentar a ciegas; reintentar con la misma clave o buscar
  el evento por `id`/`transactionId`/`iCalUID`.

### Cambios hechos fuera de la plataforma

- **Google:** `events.watch` (canal con token de verificación y renovación antes de que
  caduque) + sincronización incremental con `syncToken`.
- **Graph:** suscripción a `/users/{buzón}/events` (validación del endpoint, `clientState`,
  renovación periódica) + `delta` para leer los cambios.
- Un cambio externo (el encargado mueve o borra la cita) actualiza `InterviewBooking`,
  reprograma o cancela el recordatorio, deja un evento y una tarea para el equipo; no
  avisa a la persona candidata sin revisión.

### Pruebas antes de activarlo

- Contrato compartido con el conector local (mismas pruebas de idempotencia, carrera y propiedad).
- Calendario de pruebas del cliente y aceptación expresa antes de escribir en agendas
  reales.
