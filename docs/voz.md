# Voz en el navegador

«Hablar aquí» permite al candidato continuar la misma candidatura por voz desde el
navegador. Comportamiento general: [producto](producto.md#canales). La llamada
telefónica («Llámame») está en [canales con Twilio](twilio.md).

## Voz directa: cómo funciona

- El conversador de voz es un agente de ElevenLabs (Agents, modelo realtime) que
  recibe el brief congelado de la sesión, el historial y las herramientas autorizadas.
  No convierte cada turno en una llamada al conversador de texto; la transcripción se
  guarda en la plataforma para auditoría y continuidad.
- El navegador intercambia audio solo con la plataforma. El servidor abre el
  WebSocket privado con el proveedor y mantiene las credenciales fuera del cliente. No
  hace falta túnel ni callback entrante.
- Cada herramienta que propone el agente de voz se valida **antes** de ejecutarse:
  permisos y controles deterministas de la herramienta más una
  revisión del guardián rápido (alcance, evidencia, orden del flujo, privacidad,
  inyección) que escala al modelo principal por encima de un umbral de riesgo.
  Denegación, timeout o argumentos inválidos no ejecutan la acción. No se valida cada
  frase hablada antes de oírse. La voz directa requiere Jev activo; con `JEV_MODE=off`
  la plataforma rechaza la conexión.
- Los tickets de voz son de un solo uso y están ligados a cliente, candidatura,
  versión y transporte. El navegador no puede aportar resultados de herramientas.
- La voz requiere un consentimiento explícito registrado y siempre puede sustituirse
  por texto. Si selección toma la conversación, la voz se detiene y el candidato sigue
  por chat; al devolverla, la IA incorpora el intercambio humano.

## Selección del transporte

La API elige el transporte al conectar, en este orden:

| Prioridad | Transporte | Condiciones y recorrido |
|---|---|---|
| 1 | `eleven_direct` | Registro privado del agente compatible con la sesión y candidatura marcada `is_test`. Audio a través del servidor; herramientas validadas antes de ejecutarse. Es la opción descrita arriba |
| 2 | `gpt_live` | `GPT_LIVE_ENABLED` activo y candidatura `is_test`, si no corresponde voz directa. Audio a través del servidor con ticket de un uso |
| 3 | `speech_engine` / `custom_llm` | Configuración heredada de ElevenLabs, si no corresponde ninguno de los anteriores. El navegador recibe una URL firmada `wss://api.elevenlabs.io` y un token de sesión; requiere el callback HTTPS autenticado del servidor. El identificador de agente `seng_` selecciona `speech_engine`; el resto, `custom_llm` |

La configuración por sí sola no habilita voz directa ni GPT-Live para candidaturas
ordinarias. La marca de prueba se asigna en el flujo de simulación y en el alta del
ejemplo local, no la decide el candidato. Los transportes heredados siguen en el
código: no comparten la garantía de «sin callback» ni el mismo recorrido de revisión
que la voz directa. Si falta su configuración válida, la conexión falla y se puede
continuar por escrito; no se inicia otro proveedor de forma silenciosa.

## Configuración de voz directa

| Pieza | Dónde |
|---|---|
| Overlay de voz | `docker/eleven-direct.compose.yml`: monta `.runtime-secrets/direct/agents.json` (`ELEVEN_DIRECT_AGENTS_FILE`) y la clave de ElevenLabs como secreto |
| Límite diario | `ELEVEN_DIRECT_DAILY_LIMIT` (20 sesiones de voz por instalación y día, por defecto) |
| Guardián de herramientas | `docker/jev.compose.yml` con `JEV_MODE` = `synthetic` o `zdr`; en `synthetic` solo se envían datos sintéticos a Jev. Las candidaturas ordinarias pasan al guardián principal, sin enviar contenido a Jev. Un fallo de ambos deniega la herramienta. `off` impide abrir voz directa |
| Agente del proveedor | `python manage.py provision_direct_voice <cliente> <sesión> --output … --voice-id … --key-file …` crea o actualiza el agente privado con el prompt y las herramientas de la plataforma; hay que repetirlo cuando cambian |
| Alternativa | `GPT_LIVE_ENABLED` activa la voz con GPT-Live (`GPT_LIVE_VOICE_ID`) |

Antes de abrir una sesión directa, la plataforma contrasta la configuración del agente del
proveedor (modelo, prompt, autenticación, herramientas y ausencia de herramientas
externas que eludan el control) y rechaza la sesión si no coincide.
Las llamadas de candidaturas ordinarias exigen además grabación de audio desactivada
en el proveedor (`record_voice=false`). El aprovisionamiento usa esa política por
defecto y fija la conservación en siete días. Desactivar la grabación no elimina
las transcripciones ni las grabaciones anteriores.

## Comprobación de calidad

Las pruebas automáticas usan dobles del proveedor. Para medir la voz real (con coste y
autorización), `tools/` incluye:

| Herramienta | Uso |
|---|---|
| `prepare_voice_acceptance.py` | Crea candidaturas de prueba por la API de simulación del FDE y abre sus sesiones por la API del candidato, sin arrancar el modelo; exige una oferta y la versión publicada al 100 % esperada |
| `voice_browser_acceptance.mjs` | Recorre «Hablar aquí» en un navegador real con micrófono sintético, salida y reentrada a voz, y la conversación humana por la ficha de la candidatura |
| `measure_voice_audio.py` | Mide la reproducción PCM y exporta una muestra corta |
| `transcribe_voice_acceptance.py` | Comprueba la inteligibilidad del audio grabado (de pago) |
| `audit_direct_voice.py` | Reúne la evidencia de una prueba sin sacar enlaces ni claves de sus ficheros |

Medición local de referencia: primer audio entre 0,68 y 1,16 s (mediana 1,01 s) tras
terminar de hablar. No sustituye una prueba con micrófono y altavoces físicos.

### Protocolo de aceptación real

La preparación recibe `tenant_id output cases... --offer <clave> --expect-version
<id>`. Autentica al FDE local con `SEED_PASSWORD`, crea solo candidaturas sintéticas
sin teléfono y comprueba la versión congelada, la oferta y la ausencia de mensajes
o llamadas al modelo. El recibo privado tiene permisos `0600`, se guarda después de
cada operación y no se sobrescribe: un error exige revisarlo antes de crear otro
lote. No copia el prompt ni la sesión de otra candidatura.

Abrir el enlace en el navegador sí inicia el primer turno del modelo. Por eso el
recorrido exige autorización de gasto previa, `--live` y `VOICE_MAX_SECONDS` (de 1
a 1200 segundos por caso). Este límite de tiempo cierra el navegador, pero **no es
un tope monetario**: quien opera la prueba debe comprobar también el presupuesto y
los costes del proveedor. Los dos scripts solo admiten servidores locales. La API y
el navegador no deben conservar trazas de red ni URLs con el enlace del candidato.

Los casos incluidos usan una persona sintética para la oferta de repartidores del
ejemplo local. `normal --adaptive` cubre el recorrido hasta evaluación y posible
reserva; `injection` intenta saltarse el flujo y obtener datos ajenos;
`handoff --hr-resume` comprueba derivación, intercambio con selección y devolución a
la IA; `reconnect --reconnect` y `correction` cubren continuidad y rectificación.
`takeover --takeover --hr-resume` comprueba que selección detiene una voz activa.
Los casos se ejecutan uno a uno, con un recibo nuevo para cada lote autorizado.

El resultado del navegador separa transporte de comportamiento: audio recibido y
reproducido, latencia, errores, restauración del chat y acciones humanas no prueban
por sí solos la corrección del agente. Hay que contrastar la conversación, las citas
del resultado, las herramientas, las decisiones y los efectos en ATS/agenda; una
respuesta sin error no basta para aprobar un intento de inyección o una corrección.
Las regresiones de los controles de voz usan esos mismos controles con protocolos
doblados y no llaman a proveedores.
