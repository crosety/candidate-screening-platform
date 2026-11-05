# Privacidad y operación

Procedimientos de acceso, conservación y retirada de los datos de candidatos.

Las instalaciones locales y de pruebas trabajan con datos sintéticos (personas,
teléfonos y locales de semilla). Usar la plataforma con candidatos reales exige
antes cerrar lo descrito en [Conservación](#conservación) y en
[producción](produccion.md). El chat informa del uso de IA y de los proveedores de
modelo y voz sin afirmar residencia europea; la voz requiere una aceptación
registrada.

## Enlaces de candidato

Un enlace caduca a los 30 días. La base guarda su digest; los enlaces se derivan con
HMAC y la clave privada de la aplicación, no del digest. Los enlaces anteriores a esa
derivación siguen funcionando, pero no aparecen de nuevo en el listado del ATS hasta
que selección los renueve expresamente. El WebSocket usa un ticket distinto, válido
60 segundos y de un solo uso. Renovar o revocar no envía comunicaciones:

```sh
python manage.py candidate_link --tenant CLIENTE --application UUID --action revoke --confirm
python manage.py candidate_link --tenant CLIENTE --application UUID --action rotate --confirm
```

## Límites de consultas públicas

Las consultas de estado de SMS y llamadas tienen límites temporales independientes
de las cuotas de envío. En Redis, incrementar un contador y asegurar su caducidad
es una operación atómica. Un contador antiguo sin caducidad conserva su valor y
recupera la ventana normal en la siguiente consulta: puede seguir rechazando
peticiones hasta que expire, pero no queda bloqueado indefinidamente. Si Redis no
está disponible, el límite falla cerrado. No se deben borrar contadores ni aumentar
cuotas para resolver un error de consulta.

## Retirada auditada de un candidato

Primero se comprueba la identidad y la autoridad del solicitante fuera del chat. Se
pausan el worker y las entradas del cliente; después se obtiene la vista previa:

```sh
python manage.py redact_candidate --tenant CLIENTE --application UUID --case-ref EXPEDIENTE
```

El comando no cambia nada sin `--apply`. Con esa opción la retirada es irreversible
en la base activa. Conserva identificadores técnicos, fechas y estados mínimos para
auditoría; vacía mensajes y sus señales (incluidas citas), historial del modelo,
resultados, evidencia original no confirmada, trazas de capacidades y
copias derivadas identificables, retira contactos y revoca enlaces. Cancela efectos
pendientes de exportación de métricas y elimina sus instantáneas de voz locales;
el exportador impide nuevas transferencias de candidaturas retiradas. Cancela efectos
pendientes, cancela las entrevistas locales y sus recordatorios y retira esas
reservas de la agenda consultable. Se niega a ejecutarse con trabajos o envíos en curso. Usa los mismos
bloqueos que los turnos. Los triggers siguen prohibiendo editar el pasado: la única
excepción es vaciar el contenido sensible del sujeto con un expediente de retirada.

Reaplicar la retirada permite reparar las copias residuales cubiertas por el
procedimiento actual, también en candidaturas retiradas antes de incorporar señales
o agenda. Conserva la fecha de retirada original; solo registra una reparación si
encuentra residuos. No es una migración automática ni una limpieza de proveedores.

No es una anonimización completa: conserva identificadores técnicos y el ID externo
para impedir una reingesta accidental, que deben ser opacos (nunca direcciones de
correo). Una persona debe revisar textos derivados no enlazados (por ejemplo, una
propuesta ya copiada a un perfil) y coordinar la retirada en el ATS, los canales de
mensajería, el proveedor de voz y el del modelo; el comando no contacta con ellos.
Si se habilitó [Langfuse](metricas-llm.md), coordinar también la retirada de sus
mediciones seudónimas; no se eliminan remotamente mediante este comando.
Las copias de seguridad necesitan caducidad y control de restauración: restaurarlas
puede recuperar datos retirados.

## Conservación

No hay purga automática de candidaturas activada. Antes de tratar datos de personas
reales hay que fijar finalidad, base jurídica, responsable, contacto para derechos y
plazos de historial, trazas y copias, y separar las credenciales del rol de
migraciones de las del rol de ejecución. La caducidad del enlace no equivale a la
eliminación de los datos.
