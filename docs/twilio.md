# Canales con Twilio: WhatsApp, llamadas y SMS

Configuración y operación de los canales que usan Twilio. Comportamiento para el
candidato: [producto](producto.md#canales). Tratamiento de datos y consentimiento:
[privacidad](privacidad-operacion.md).

Los tres canales están **desactivados** salvo que exista su configuración privada y
esté vigente. Cada canal conserva su política de destinatarios y consentimiento.

### Alcance operativo de llamadas y SMS

Las llamadas admiten candidaturas ordinarias y de prueba: desde su enlace privado
vigente, el candidato introduce su móvil español y solicita expresamente que le
llamen. No necesita un alta previa del teléfono ni la marca `is_test`. Se aceptan
`+346…` y `+3471…` a `+3474…`, con nueve
dígitos nacionales; no fijos, emergencias, números especiales ni otros países.

La vista pública temporal del candidato admite también esas candidaturas ordinarias
mediante su enlace personal vigente. Conserva el cliente, origen, rutas permitidas
y caducidad configurados; no publica la consola ni crea nuevas rutas. Los permisos
de cada canal se siguen verificando en sus propios endpoints.

Los SMS mantienen su política independiente: candidatura `is_test`, móvil en
`participants` y consentimiento SMS. Permitir una llamada no autoriza ni habilita
un SMS posterior a otro destinatario. Ambos canales usan la cuenta de voz y el
remitente contratado `+1`. Las restricciones de WhatsApp y de voz en navegador no
cambian. La oferta debe tener voz configurada y un agente compatible registrado;
la apertura a candidaturas ordinarias no provisiona agentes automáticamente.
Para candidaturas ordinarias, la configuración remota de voz debe confirmar
`record_voice=false`: se verifica antes de marcar y de iniciar audio. El comando
de aprovisionamiento configura los agentes nuevos sin grabación y actualiza los
antiguos cuando les falta esta política. Esto no borra grabaciones anteriores ni
elimina la conservación de transcripciones.

## Configuración

Cada canal tiene su fichero privado en `.runtime-secrets/` (modo `600`, ignorado por
Git), montado solo en `api` y `worker` mediante su overlay de Compose. Al activar un
canal, añade su overlay a los que ya estén en uso sin sustituirlos.

| Canal | Fichero (variable) | Overlay | Listener público |
|---|---|---|---|
| WhatsApp | `TWILIO_CONFIG_FILE` | `docker/twilio.compose.yml` | `web.twilio_ingress` |
| Llamadas | `TWILIO_VOICE_CONFIG_FILE` | `docker/telephone.compose.yml` | `web.telephone_ingress` |
| SMS | `TWILIO_SMS_CONFIG_FILE` | `docker/sms.compose.yml` | callback de estado en la API |

Campos comunes: `tenant_id`, credenciales de la cuenta, remitente E.164,
`public_base_url` HTTPS, `participants` (teléfonos permitidos de mensajería), `enabled` y
`expires_at` con zona horaria cuando la apertura es temporal. WhatsApp añade
`max_outbound_per_day` (60 por defecto, máximo 100); las llamadas,
`max_attempts_per_mobile`; los SMS, `max_segments` y presupuesto. No copies estos
ficheros a Git, capturas, logs ni chats.

En la configuración de voz, `participants` es opcional y puede estar vacío: solo
se conserva para la política de SMS que comparte esa cuenta, no limita llamadas.

`python manage.py twilio_status` comprueba la configuración local y, con `--remote`,
la cuenta.

## Exposición a Internet

Solo se publica el listener del canal, mediante HTTPS y con caducidad: nunca la API
general (8000) ni los frontales (5173, 5174). El listener de WhatsApp solo admite
`POST` sin query a `…/twilio/{tenant_id}/inbound` y `…/status`; cualquier otra ruta
responde 404 y un `POST` sin firma, 403. La firma se valida contra el origen
configurado, no contra `Host` ni cabeceras de proxy: cambiar de origen exige
actualizar la configuración y los callbacks en Twilio. Al vencer `expires_at`, el
servidor rechaza entradas y salidas aunque el túnel siga abierto.

Ejemplo de arranque local del listener de WhatsApp:

```sh
TWILIO_CONFIG_FILE=.runtime-secrets/twilio.json DJANGO_DEBUG=false \
  uv run uvicorn web.twilio_ingress:application --host 127.0.0.1 --port 8002 \
  --lifespan off --no-access-log --log-level warning
```

## WhatsApp

Recorrido: el candidato escribe → aviso de IA y privacidad → menú de ofertas
abiertas (hasta diez) → `ACEPTO <n>` o `ACCEPT <n>`, que fija también el idioma →
nombre y apellidos → entrevista. La candidatura aparece en la bandeja normal; el
nombre del perfil de WhatsApp no se toma como identidad verificada. El perfil queda
fijado al iniciar la sesión.

- Pedir una persona deriva al equipo. Quien la toma responde desde la ficha y el
  mensaje se encola al mismo móvil; al devolver la conversación, la IA incorpora ese
  intercambio.
- `NUEVA` abre otra selección solo si la candidatura anterior está cerrada. `BAJA`
  detiene futuros envíos y no se revoca con un saludo ni con `NUEVA`. Una negativa
  explícita («No quiero») no crea candidatura.
- Fuera de la ventana de 24 horas desde el último mensaje del candidato no se envía.
  Sin plantillas, adjuntos ni campañas.
- Salidas de hasta 1600 caracteres y con un intervalo mínimo entre envíos; un
  timeout cuenta como intento.

## Llamadas

«Llámame» abre un formulario donde el candidato confirma su móvil y su
consentimiento. Se reserva el presupuesto y se hace **un único intento**: un
resultado dudoso nunca se reintenta. Se conservan diez intentos globales y una
reserva de 2 USD por intento, con un techo acumulado de 20 USD, sin reinicios por
fecha ni por resultado. `max_attempts_per_mobile` limita tanto cada móvil como cada
candidatura (por defecto diez): cambiar el número no elude el cupo de la candidatura.
Las reservas concurrentes se serializan; una conversación no puede tener dos
solicitudes pendientes. La duración máxima sigue siendo cinco minutos por llamada.
Se respeta la franja de descanso de 22:00 a 08:00 en la zona horaria de la
candidatura. Se comprueba al solicitar y al despachar; si la espera cruza a esa
franja, se cancela, sin programar una llamada para otro momento automáticamente.
La voz configurada, el registro local y el guardián se comprueban antes de reservar;
la deriva o un fallo remoto posterior siguen consumiendo el intento reservado.

La solicitud, el inicio y la conexión verifican el enlace, la sesión activa y la
ausencia de toma humana. Los callbacks firmados quedan ligados al intento y al
destino consentido. El audio va por Media Streams al runtime de voz, con las mismas herramientas
validadas que el resto de canales; el agente se presenta como asistente de IA de la
empresa y puede pausar la llamada para seguir después. Terminar una llamada no cierra
la candidatura. El modelo nunca puede iniciar una llamada.

La disponibilidad general depende del presupuesto global y del cupo de la
candidatura; el cupo del móvil se comprueba al enviar el formulario. Agotar un móvil
no deshabilita el servicio para los demás candidatos. No se verifica la titularidad
del móvil mediante un código: el enlace personal y el consentimiento no equivalen
a una prueba de titularidad; los límites reducen, pero no eliminan, el abuso.

## SMS con enlace

Aviso de un solo sentido con el enlace personal, no un chat. Se envía por invitación
desde la ficha de selección o a petición del candidato, y una vez tras una llamada
atendida si el consentimiento sigue vigente. Cada aviso reserva sus segmentos dentro
del presupuesto; reintentos y resultados ambiguos no vuelven a enviar.

## Estados y fallos

- `pending` no acredita entrega. `sent`, `delivered` y `read` llegan del proveedor y
  se muestran junto al mensaje en la conversación del equipo.
- `unknown` significa que la petición pudo llegar: **no reenviar automáticamente**.
  `dispatching` tras una caída también requiere revisar el estado en Twilio.
- Un destino que no sea un móvil español válido se rechaza antes de crear el
  intento o contactar con el proveedor. La disponibilidad no garantiza que queden
  intentos para ese móvil concreto.
- Los trabajos fallidos se revisan por su ID; su payload no incluye texto ni teléfono.
- El límite de salidas no impide que el proveedor cobre las entradas: vigila saldo y
  uso, conserva las listas de mensajería y desactiva el canal al terminar.
- La retirada de un candidato limpia también el texto pendiente en estas colas y
  cancela sus trabajos; no borra copias en Twilio ni en backups.

Fuentes oficiales (consultadas el 27/10/2026):
[Sandbox](https://www.twilio.com/docs/whatsapp/sandbox),
[firmas](https://www.twilio.com/docs/usage/security),
[error DNS 11210](https://www.twilio.com/docs/api/errors/11210),
[precios de voz en España](https://www.twilio.com/en-us/voice/pricing/es),
[integración con ElevenLabs](https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/native-integration).

Privacidad de voz (consultada el 31/10/2026):
[grabación y conservación](https://elevenlabs.io/docs/eleven-agents/customization/privacy)
y [actualización del agente](https://elevenlabs.io/docs/eleven-agents/api-reference/agents/update).
