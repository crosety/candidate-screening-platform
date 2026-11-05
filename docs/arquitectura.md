# Arquitectura

Mapa técnico del repositorio: qué responsabilidad tiene cada pieza y dónde consultar
su implementación. El comportamiento para candidatos, selección y FDE está en
[Producto](producto.md); los términos, en el [glosario](../GLOSSARY.md).
Las firmas, modelos, rutas y valores por defecto se
consultan en sus fuentes, no se mantienen como una segunda especificación aquí.

## Procesos y dependencias

La instalación local separa API, worker y frontales. Su definición ejecutable es
[`docker-compose.yml`](../docker-compose.yml); la pila de pruebas de navegador está
en [`docker-compose.e2e.yml`](../docker-compose.e2e.yml).

| Componente | Responsabilidad |
|---|---|
| API Django, Ninja y Channels | HTTP, autenticación, WebSocket y entrada a los casos de uso |
| Worker | Trabajos persistidos: evals, outbox, recordatorios y mantenimiento |
| PostgreSQL con pgvector | Datos del negocio, cola de trabajos y búsqueda vectorial |
| Redis | Caché, tickets efímeros, límites de uso y comunicación entre conexiones |
| Consola React | Configuración del FDE y trabajo de selección |
| Chat React | Conversación y controles de canal del candidato |
| Servidor MCP de registro | Conector local de ejemplo; no acredita una integración externa |

Los frontales están en [`apps/console`](../apps/console/) y
[`apps/chat`](../apps/chat/). Versiones y comandos se definen en los manifiestos,
el [Makefile](../Makefile) y [Convenciones](convenciones.md). La topología local no
equivale a un despliegue productivo: véanse [Producción](produccion.md) y
[Despliegue y escalado](despliegue-y-escalado.md).

## Capas y composición

| Ubicación | Responsabilidad |
|---|---|
| [`screening/domain`](../src/screening/domain/) | Reglas puras: configuración, validación, riesgo, autonomía, encaje y calendario |
| [`screening/application`](../src/screening/application/) | Casos de uso y contratos con el exterior |
| [`adapters`](../src/adapters/) | Modelos, persistencia, secretos, canales y dobles de prueba |
| [`connectors`](../src/connectors/) | Comunicación con sistemas del cliente |
| [`web`](../src/web/) | API, modelos y migraciones Django, permisos y entradas por canal |
| [`worker`](../src/worker/) | Consumo y ejecución de trabajos |
| [`screening/platform/container.py`](../src/screening/platform/container.py) | Construcción de servicios y elección de adaptadores |

Las dependencias del núcleo apuntan hacia el dominio. Los contratos verificables
están en [`.importlinter`](../.importlinter), y los puertos y registros intercambiados
entre capas en [`ports.py`](../src/screening/application/ports.py). La persistencia
Django los implementa en [`adapters/persistence`](../src/adapters/persistence/).
Los casos de uso no importan Django ni SDKs de modelos.

La mejora continua y el mantenimiento de perfiles se coordinan desde
[`web/profiles`](../src/web/profiles/), con acceso al ORM de Django.

## Configuración, versiones y publicación

[`ProfileContent`](../src/screening/domain/profile.py) contiene la configuración
común del cliente y sus ofertas. `profile_for_offer` compone la configuración
efectiva de una oferta. Los esquemas de campos, conexiones, permisos y autonomía
viven en ese modelo; no hay un módulo de negocio por cliente.

[`ProfileService`](../src/screening/application/profiles.py) gestiona el borrador,
la publicación, las aprobaciones, los pilotos y la vuelta atrás. Las comprobaciones
y el riesgo están separados en [`checks.py`](../src/screening/domain/checks.py) y
[`diff.py`](../src/screening/domain/diff.py).

La revisión del borrador evita guardar sobre cambios ajenos. Publicar crea una
versión; una sesión conserva la versión con la que empezó. La puerta de evals se
conecta al servicio desde el contenedor según la configuración de la instalación.
Su implementación es [`evals_gate.py`](../src/adapters/persistence/evals_gate.py):
comprueba oferta, personas, resultados, configuración y arnés, no solo una media.
Los dobles y casetes no satisfacen esa puerta.

El recorrido de publicación y sus condiciones están en [Producto](producto.md).

## Datos, aislamiento e historial

Los modelos y migraciones están agrupados por responsabilidad:

| Área | Fuente |
|---|---|
| Clientes, miembros, segmentos y auditoría | [`web/tenancy`](../src/web/tenancy/) |
| Borradores, versiones, publicaciones y conocimiento | [`web/profiles`](../src/web/profiles/) |
| Candidaturas, sesiones, mensajes, enlaces, canales y entrevistas | [`web/conversations`](../src/web/conversations/) |
| Evaluaciones, decisiones y efectos pendientes al ATS | [`web/decisions`](../src/web/decisions/) |
| Evals y trazas | [`web/evals`](../src/web/evals/) · [`web/tracing`](../src/web/tracing/) |
| Trabajos y conectores locales | [`web/jobs`](../src/web/jobs/) · [`web/simulated`](../src/web/simulated/) |

El aislamiento de las tablas de negocio combina `tenant_id`, RLS forzada y
[`tenant_context`](../src/web/tenancy/context.py), que fija el cliente dentro de una
transacción. Las políticas y los triggers de solo añadir se crean desde
[`rls.py`](../src/web/tenancy/rls.py). Versiones, eventos de conversación e historial
del modelo no se corrigen sobrescribiéndolos.

No toda tabla con `tenant_id` usa RLS: miembros, cola de trabajos y enlaces del
candidato tienen funciones de entrada o de plataforma. Las excepciones y el rol de
base de datos se comprueban en [`test_rls.py`](../tests/api/test_rls.py). No deben
usarse como patrón para añadir datos de negocio sin aislamiento.

Dentro de un cliente, los permisos de selección se acotan además por segmentos:
[`permissions.py`](../src/web/tenancy/permissions.py) comprueba pertenencia y rol;
[`scope.py`](../src/web/tenancy/scope.py) limita candidaturas y sesiones.
Retención, borrado y operación están en [Privacidad](privacidad-operacion.md).

## Entrada del candidato y conversación

El enlace personal es una credencial de acceso. La columna `CandidateLink.token`
guarda su resumen criptográfico, no el token utilizable. La generación, resolución,
caducidad y revocación están en [`links.py`](../src/web/conversations/links.py) y el
[repositorio de conversaciones](../src/adapters/persistence/conversations.py).

El chat abre la sesión mediante HTTP y recibe un ticket efímero para el WebSocket.
[`ChatConsumer`](../src/web/conversations/consumers.py) consume ese ticket de un
solo uso, vinculado a la sesión, y vuelve a comprobar la vigencia del enlace. El
WebSocket no recibe el enlace personal como parámetro `token`.
Los tickets y límites de uso están en [`security.py`](../src/web/core/security.py).

[`ConversationService`](../src/screening/application/conversation.py) abre o retoma
la sesión, serializa sus turnos, conserva mensajes e historial y coordina la
derivación humana. El flujo de texto es:

1. Resolver la candidatura y la versión, y construir su `RunContext`.
2. Preparar el turno con historial, oferta, canal, reloj y señales de idioma y tono.
3. Ejecutar el conversador con las herramientas que permite ese contexto.
4. Revisar la respuesta, guardar los nuevos mensajes y devolver el estado resultante.

El perfil y el prompt base se conservan en la sesión. El turno añade contexto
operativo y aplica la política de presentación de
[`current_conversation_prompt`](../src/screening/application/prompts.py), sin
reescribir el registro guardado. La presentación actual no anuncia una palabra
clave para pedir una persona; la derivación solicitada por el candidato sigue
disponible. Las reglas de tono y seguimiento están en
[Reenganche e idiomas](reenganche-y-tono.md).

## Agentes, herramientas y modelos

[`RunContext`](../src/screening/domain/run_context.py) identifica cliente,
candidatura, oferta, versión, papel, permisos y alcance. El modelo no decide esos
permisos. El catálogo está en [`capabilities.py`](../src/screening/domain/capabilities.py);
[`CapabilityGateway`](../src/screening/application/capability_gateway.py) valida
contexto, concesiones y autonomía antes de ejecutar una herramienta y registra su
resultado.

El bucle del conversador vive en
[`runner.py`](../src/adapters/agent_langchain/runner.py). Los prompts están en
[`prompts.py`](../src/screening/application/prompts.py), y las revisiones del
guardián, la validación de herramientas y las trazas, en
[`middleware.py`](../src/adapters/agent_langchain/middleware.py).
El guardián puede pedir correcciones; si la respuesta sigue vetada tras el límite
de revisiones, la conversación se deriva. El límite se consulta en
`GuardianMiddleware.MAX_REVISIONS`, no en una copia de su algoritmo.

El guardián es una cadena que se compone en
[`container.py`](../src/screening/platform/container.py) en modo `openai`. Con
`JEV_MODE` en `synthetic` o `zdr`, primero responde Jev, un modelo de decisión rápido y barato
([`jev_guard.py`](../src/adapters/agent_langchain/jev_guard.py)); los casos dudosos,
los fallos y los tiempos agotados pasan al guardián de Terra. Con `JEV_MODE=off`,
el valor por defecto, revisa siempre Terra. En `synthetic` solo se envían a Jev
candidaturas sintéticas. Latencias medidas en
[Despliegue y escalado](despliegue-y-escalado.md).

El contenedor admite `fake`, `openai`, `bedrock` y `replay`. `fake` usa dobles
de modelos, pero no sustituye la base de datos por memoria. `replay` construye los
adaptadores de la rama Bedrock: la reproducción sin red depende de los casetes del
arnés de pruebas, no del nombre del modo. Modelos, esfuerzos y configuración
operativa se consultan en [Inferencia](openai-runtime.md) y
[`adapters/llm`](../src/adapters/llm/).

La voz directa tiene un recorrido distinto del texto: validar herramientas no
equivale a revisar cada frase hablada. Sus modalidades, guardián y requisitos están
en [Voz](voz.md). WhatsApp, llamadas y SMS se describen en [Twilio](twilio.md).

## Evaluación, decisiones y efectos externos

[`AssessmentService`](../src/screening/application/assessment.py) recibe una
extracción estructurada del evaluador y valida tipos y citas con
[`result.py`](../src/screening/domain/result.py). Puede pedir aclaración antes de
crear una decisión. Cuando cierra para revisión humana con campos no validados, no
los conserva como valores confirmados; la extracción queda separada en el evento
de auditoría. No se reescriben resultados históricos.

El veredicto no es la decisión aplicada. Las reglas están en
[`autonomy.py`](../src/screening/domain/autonomy.py): solo `qualify` puede aplicarse
automáticamente con el nivel necesario y sin alertas; el servicio exige además ATS
con escritura. Descartes, bolsa y revisión se proponen al equipo. La confirmación o
corrección humana pasa por [`InboxService`](../src/screening/application/inbox.py).

Las escrituras al ATS se registran en la outbox antes de enviarse.
[`OutboxService`](../src/screening/application/outbox.py) despacha el efecto y
comprueba la respuesta; la [reconciliación](../src/web/decisions/reconcile.py)
gestiona trabajos que ya no pueden continuar. El registro local de un efecto no
prueba su recepción por un sistema externo. Contrato y comportamiento del ATS:
[Integración](integracion-ats.md) y [OpenAPI del ATS](ats/openapi.yaml).
Las reservas y sus cambios tienen su propio servicio y se describen en
[Agenda](agenda.md).

## API, worker y comprobaciones

La API se compone en [`web/api.py`](../src/web/api.py), con rutas adicionales en
[`web/urls.py`](../src/web/urls.py). La consola usa autenticación de sesión de Django;
los permisos de cliente y segmento se comprueban aparte. La especificación se
sirve en `/api/v1/docs`; su exportación y el cliente TypeScript están en
[`packages/api-client`](../packages/api-client/). Los esquemas de cada área son la
referencia para campos y respuestas. El acceso público limitado por canal se
configura aparte en [`external_asgi.py`](../src/web/external_asgi.py), no exponiendo
toda la consola.

La cola se guarda en PostgreSQL. [`worker/loop.py`](../src/worker/loop.py) reclama
trabajos con bloqueo de filas, ejecuta el manejador fuera de esa transacción y
gestiona reintentos y trabajos interrumpidos. Los manejadores están en
[`handlers.py`](../src/worker/handlers.py); cada operación de datos abre su contexto
de cliente. No se mantiene una transacción abierta durante una eval o una llamada
externa completa.

Los usos de modelos y capacidades pasan por `TraceSink`; la implementación
persistente está en [`adapters/persistence/tracing.py`](../src/adapters/persistence/tracing.py).
Las evals usan [`EvalService`](../src/screening/application/evals.py), personas
sintéticas y un [hash del arnés](../src/adapters/eval_harness.py). Una prueba con
dobles comprueba la orquestación, no el comportamiento del modelo ni la entrega
real de un canal.

Los comandos de comprobación están en el [README](../README.md#checks).
`make check` verifica el código y sus
contratos; las pruebas de navegador usan la pila e2e aislada. Las evals y
comprobaciones con proveedores reales son ejecuciones separadas, con autorización
y presupuesto.
