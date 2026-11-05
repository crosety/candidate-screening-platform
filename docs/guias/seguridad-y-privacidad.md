# Guía · Seguridad y privacidad

**Propósito.** Proteger a los candidatos, a los clientes y a la propia operación.
Tratamos datos personales en un proceso de selección, sujeto al RGPD y a la
regulación de IA en empleo. **Alcance:** autenticación y permisos, datos de
candidatos, logs y trazas, canales externos, webhooks, secretos, configuración y
cualquier texto que el agente lea o escriba.

Operación diaria y procedimientos: [privacidad y operación](../privacidad-operacion.md).

## Principios

1. **Mínimo dato, mínimo acceso.** Se pide, guarda, muestra y registra solo lo
   necesario para la decisión de selección.
2. **Todo lo que escribe el candidato es dato, nunca instrucción.** Ni el agente ni el
   evaluador ni el juez obedecen texto de una conversación.
3. **Las personas deciden.** El agente propone; las decisiones con efecto laboral las
   confirma una persona según el dial de autonomía del cliente.
4. **Fallar cerrado.** Ante una duda de permisos, firma, consentimiento o
   presupuesto, la plataforma no actúa y lo registra.

## Invariantes

- **SEG-1 · Secretos fuera del repositorio y de los registros.** Ni claves, ni
  tokens, ni contraseñas, ni enlaces de acceso en código, fixtures, casetes, logs,
  trazas, errores de API o documentación. Se leen del entorno o del almacén privado
  (`.env`, `.runtime-secrets/`, `SecretStore`), que nunca se versionan ni se imprimen.
- **SEG-2 · Autenticación y rol en cada endpoint.** Todo endpoint privado comprueba
  sesión, pertenencia al cliente y rol (`admin`, `fde`, `recruiter`) y respeta los
  segmentos permitidos de la persona. Un recurso de otro cliente responde 404.
- **SEG-3 · Aislamiento por cliente** con RLS (véase `ARQ-3`). Las trazas, eventos y
  evals también son datos del cliente.
- **SEG-4 · Enlaces y tokens del candidato** se guardan solo como resumen
  criptográfico, caducan y no aparecen en URLs registradas ni en logs.
- **SEG-5 · Webhooks firmados.** Toda entrada de un proveedor externo verifica su
  firma antes de leer el contenido, es idempotente y no revela detalles internos en
  la respuesta.
- **SEG-6 · Consentimiento antes de contactar.** Llamadas, SMS y mensajes salientes
  exigen consentimiento registrado del candidato, respetan horas silenciosas, la
  política de destinatarios de cada canal, límites de intentos y presupuesto reservado.
  Mensajería mantiene listas permitidas. Una llamada solicitada expresamente desde
  un enlace privado vigente admite el
  móvil español indicado sin alta previa ni marca de prueba; no habilita otros canales.
  Los envíos de prueba del agente requieren autorización explícita y no se deducen
  de esta elegibilidad del producto.
- **SEG-7 · Identificación como IA y vía a una persona.** El candidato sabe que habla
  con un asistente de IA de la empresa y puede pedir una persona en cualquier momento.
- **SEG-8 · Sin datos sensibles ni inferencias.** El agente no pregunta ni infiere
  salud, origen, religión, edad, situación familiar u otras categorías especiales, ni
  rasgos de personalidad. Ánimo e idioma son observaciones para el tono, nunca
  criterios de selección.
- **SEG-9 · Defensa ante inyección.** El texto del candidato se escapa al construir
  prompts, el guardián revisa cada respuesta antes de enviarla y el evaluador marca
  intentos de cambiar el resultado o de obtener datos de terceros.
- **SEG-10 · Borrado y retención.** El borrado de un candidato pasa por el servicio de
  privacidad, que concilia los envíos en curso y conserva solo lo que la ley exige.
- **SEG-11 · Logs sin contenido personal completo.** No se registran conversaciones
  completas, teléfonos, correos ni documentos; los identificadores sí.

## Recomendaciones

- **SEG-R1.** Valida toda entrada externa con esquemas estrictos (Pydantic/Ninja) y
  rechaza campos desconocidos.
- **SEG-R2.** Mensajes de error útiles para la persona y genéricos sobre la
  infraestructura: sin nombres de proveedores, trazas ni rutas internas.
- **SEG-R3.** Toda capacidad nueva del agente declara sus permisos y su nivel de
  autonomía por defecto; empieza en el más restrictivo.
- **SEG-R4.** Al añadir una dependencia, revisa su procedencia y fija su versión.
- **SEG-R5.** Cualquier exposición a Internet (túneles, ingress) se limita a las rutas
  imprescindibles, con caducidad y sin abrir la consola ni la API general.

## Señales de deuda

- Comprobaciones de rol duplicadas a mano en cada endpoint en lugar de un mismo punto.
- Campos de texto libre que acaban guardando datos personales sin necesidad.
- Configuración privada que se copia entre entornos sin inventario.
- Excepciones de seguridad «temporales» sin fecha de retirada.

## Qué revisa el evaluador

- Secretos, tokens o datos personales en el diff, en logs nuevos o en fixtures.
- Endpoints, comandos o tareas del worker sin comprobación de rol o fuera de
  `tenant_context`.
- Canales salientes sin consentimiento, idempotencia, límites o presupuesto.
- Prompts o herramientas que traten texto del candidato como instrucción o que
  permitan al modelo saltarse permisos.
- Preguntas o inferencias sobre categorías sensibles; uso de señales del candidato
  como criterio de decisión.
- Webhooks sin verificación de firma o con respuestas que revelan detalles internos.
