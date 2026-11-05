# Métricas de modelos

La instalación puede enviar a un proyecto privado de Langfuse las mediciones de todos
los papeles de modelo: conversador, guardián, evaluador, juez, simulador, copiloto,
señales y asistente del reclutador; también Jev, embeddings y GPT Live cuando se usan.
La fuente de las llamadas de texto sigue siendo `LlmCall` en la base local.

Cada llamada produce una observación con modelo, esfuerzo, papel, tokens de entrada,
salida y caché, coste en USD y duración. Las sesiones se agrupan mediante un resumen
criptográfico del cliente y la sesión; cada observación tiene una identidad estable.
Los costes de texto son los calculados por el medidor local, no una factura del
proveedor. La duración local se reconstruye desde la fecha de registro y la latencia;
no equivale a tiempo hasta el primer token. No se exportan prompts, respuestas,
documentos, teléfonos, nombres, correos, audio, resultados de selección ni credenciales.

## Abrir una sesión desde la consola FDE

En **Trazas técnicas → conversación → Abrir en Langfuse**, el FDE o administrador
abre en otra pestaña las métricas de esa misma sesión. También puede llegar desde
una candidatura mediante **Abrir diagnóstico técnico**. El enlace usa el proyecto y
la región configurados, y el mismo identificador seudónimo que la exportación; no
incluye nombres, teléfonos, el enlace del candidato ni claves de acceso. El navegador
no envía la URL de origen. Langfuse exige iniciar sesión y tener permiso en su proyecto;
el botón no concede acceso ni inicia exportaciones o inferencias.

La exportación es asíncrona: una sesión nueva puede no tener métricas todavía. Las
mediciones históricas siguen siendo accesibles aunque se desactive la exportación.
Si falta una configuración válida, o la candidatura está retirada, no aparece el
botón. El reclutador y el candidato no reciben este enlace. Se utiliza el proyecto
configurado actualmente; si se cambia de proyecto, las métricas anteriores no se
migran y deben consultarse en el destino original. El contenido de la conversación
permanece en Orbio: este enlace no amplía los datos exportados.

## Entrega y errores

El chat solo guarda el registro y encola un trabajo. El worker verifica que la clave
pertenece exclusivamente al proyecto configurado y envía las métricas mediante
OTLP/HTTP JSON, con ingesta v4. No se añade una dependencia de instrumentación global:
el adaptador utiliza `httpx` y una lista explícita de campos, sin capturar contenido.
Los fallos de red, entrega parcial o credenciales se conservan como errores genéricos
del trabajo y no interrumpen la entrevista. Hay diez intentos con espera progresiva;
agotarlos deja el trabajo en `dead`, no en entregado. Desactivar la integración conserva
el trabajo, pero no pausa el contador de intentos: puede acabar agotado. Reactivar no
recupera automáticamente trabajos `dead`. Cambiar de proyecto no redirige los
pendientes al nuevo destino. La cola comparte capacidad con trabajos operativos;
conviene supervisar su acumulación antes de ampliar volumen.

Langfuse v4 no deduplica por ID. Antes de enviar, se consulta la recepción por ID y
rango temporal. Si ya consta completa, no se repite la entrega. Antes del POST se
persiste el estado `uncertain`; una respuesta correcta lo convierte en `delivered`.
Un timeout, una respuesta parcial o una caída después de iniciar el envío solo
permiten consultar recibos, nunca repetir automáticamente el POST. Si la recepción
no se confirma, el trabajo acaba en `dead` para conciliación manual, aunque realmente
no llegara a enviarse. Se prioriza no duplicar consumo frente a una entrega a ciegas.
La identidad estable permite conciliar; no constituye una garantía del proveedor.
Una aceptación HTTP no sustituye la comprobación de las observaciones en Langfuse.
La integración no activa evaluaciones automáticas ni llamadas adicionales a modelos.
`make test` no utiliza la cuenta de Langfuse.

## Voz del navegador y teléfono

Al terminar una conexión de ElevenLabs, el mismo recorrido de navegador y teléfono
encola una consulta de métricas del proveedor, con una espera inicial de 30 segundos.
Si el procesamiento sigue pendiente se reintenta. Solo las mediciones seleccionadas
se conservan en `VoiceMeasurement`, aislado por cliente mediante RLS. El trabajo
solo conserva referencias y destino; el historial recibido se descarta y nunca se reenvía.

La observación `voice` muestra el modelo comunicado por el proveedor (o
`unknown-voice-model` si falta o intervienen varios), duración de toda la llamada y
coste fiat del servicio de voz.
**Ese coste no incluye Twilio y no es un desglose de coste por tokens.** Si el proveedor
no informa de tokens o coste, se deja ausente, no se estima ni se convierte en cero.
Los desgloses `voice_breakdown`, cuando existen, conservan por separado las categorías
del proveedor como metadatos, sin sumarlas al total facturable para evitar solapamientos.
No se descarga audio ni se inicia ninguna llamada para recoger métricas.
GPT Live también conserva duración de llamada y coste del medidor local; sus ceros
históricos de tokens significan ausencia de medida y no se exportan como consumo cero.

## Configuración

Crear un proyecto privado y una clave exclusiva de ese proyecto. Guardar fuera de Git
un JSON privado, legible solo por el usuario del servicio, con estos campos:

```json
{"host":"https://cloud.langfuse.com","project_id":"ID_DEL_PROYECTO","public_key":"CLAVE_PUBLICA","secret_key":"CLAVE_SECRETA"}
```

Configurar `LANGFUSE_PROJECT_ID`, `LANGFUSE_HOST`, `LANGFUSE_CONFIG_FILE` (ruta absoluta al fichero),
`LANGFUSE_ENABLED=true` y `LANGFUSE_ENVIRONMENT=local` en el entorno privado.
Se admiten los destinos oficiales EU, US y Japón. La región se elige al crear el
proyecto; esta integración no traslada datos entre regiones. `LANGFUSE_HOST` usa EU
por defecto (`https://cloud.langfuse.com`); para US usar `https://us.cloud.langfuse.com`
y para Japón `https://jp.cloud.langfuse.com`. Debe coincidir con `host` en el JSON
privado: el worker rechaza una discrepancia para no exportar a una región distinta
de la que abre la consola. API y worker deben compartir estos ajustes no secretos.

Con Compose, añadir `-f docker/langfuse.compose.yml` **a los overlays ya utilizados**;
no sustituir los overlays de voz, telefonía o canales. Solo el worker necesita montar
la clave de Langfuse. Reiniciar los procesos de backend para recoger la configuración.
Aplicar primero `python manage.py migrate`; añade la tabla de mediciones de voz con RLS.
No activar la exportación de datos de candidatos reales sin definir antes los plazos
de retención y el acceso al proyecto. Los identificadores seudónimos siguen siendo
datos enlazables desde la instalación y requieren la misma gobernanza.

## Conciliación de mediciones locales

Para recuperar registros de texto anteriores o una cola que no pudo escribirse:

```sh
python manage.py export_metrics --tenant CLIENTE --since 2026-10-31T00:00:00Z --limit 100
python manage.py export_metrics --tenant CLIENTE --since 2026-10-31T00:00:00Z --limit 100 --apply
```

Sin `--apply` solo cuenta. El límite máximo es 1.000. `--retry` junto a `--apply`
reactiva únicamente los trabajos agotados de esas mediciones y del proyecto actual.
No vuelve a ejecutar inferencias ni modifica las mediciones. Para voz, revisar los
trabajos `eleven_metrics`; su instantánea numérica aislada permite reintentar sin consultar
otra vez el historial cuando ya se obtuvo. Una caída del proceso antes de encolar
el cierre de voz requiere conciliar los eventos de voz del servidor manualmente.
Para un trabajo de voz agotado, el operador debe comprobar su cliente, sesión y proyecto,
y reencolar ese ID concreto con intentos a cero; `export_metrics --retry` solo cubre texto.
Reactivar un trabajo `uncertain` vuelve a consultar su recibo; no autoriza un nuevo
POST. Un operador debe resolver expresamente la incertidumbre antes de restablecer
su estado de entrega, después de comprobar el destino. La consulta de recibos depende
de la ventana de lectura del plan contratado; no poder ver una observación antigua
no demuestra que nunca se enviara.
El comando selecciona los primeros registros del intervalo, incluidos los ya enviados.
No tiene cursor: para superar 1.000 registros o empates temporales se requiere una
conciliación operativa explícita, no repetir el mismo comando esperando otro lote.

La retirada local cancela exportaciones pendientes y elimina las instantáneas de voz.
Los manejadores vuelven a comprobar que la candidatura no está retirada, incluso al
conciliar o procesar trabajos antiguos. Las métricas ya enviadas requieren retirada
separada en Langfuse: véase [privacidad](privacidad-operacion.md).

El medidor anterior solo registra llamadas que alcanzaron su punto de medición; no
reconstruye tokens de peticiones fallidas antes de recibir uso. La exportación no
inventa esas mediciones ni convierte el número de observaciones en todas las
peticiones intentadas al proveedor.

Referencias de contrato: [OTLP de Langfuse](https://langfuse.com/integrations/native/opentelemetry),
[inmutabilidad y duplicados](https://langfuse.com/faq/all/tracing-data-updates)
y [detalle de conversación de ElevenLabs](https://elevenlabs.io/docs/api-reference/conversations/get).
