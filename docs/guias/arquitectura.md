# Guía · Arquitectura

**Propósito.** Que la plataforma siga siendo una sola, configurable por cliente, fácil
de cambiar y segura de escalar. **Alcance:** cualquier cambio en `src/`, `services/`,
migraciones, `.importlinter`, `pyproject.toml` o la forma de los datos.

Referencia técnica detallada: [arquitectura](../arquitectura.md) y
[convenciones](../convenciones.md). Esta guía fija lo que no se negocia.

## Principios

1. **Arquitectura en capas (onion / puertos y adaptadores).** Las dependencias apuntan
   hacia dentro: el dominio no conoce a nadie; la aplicación conoce el dominio y
   declara puertos; los adaptadores implementan puertos; `screening.platform`
   compone todo. La lógica de negocio vive dentro; los frameworks, fuera.
2. **Configurado, no programado por cliente.** Un cliente nuevo es un perfil
   (brief, ofertas, esquema del resultado, fuentes, conexiones, permisos), no código.
3. **La plataforma pone los bordes; el modelo, el contenido.** Cuándo, cuántas veces,
   por qué canal, con qué permisos y qué se guarda lo decide código determinista. Qué
   se le dice al candidato lo decide el agente. Véase [Agentes e IA](agentes-e-ia.md).
4. **Módulos profundos.** Interfaces pequeñas con mucho comportamiento detrás. Un
   puerto existe porque hay al menos dos adaptadores reales o porque es la costura de
   test de un sistema externo, no por simetría.
5. **El historial no se reescribe.** Versiones, eventos y mensajes del modelo se
   añaden; los cambios se expresan como nuevos registros.

## Capas

| Paquete | Rol | Puede importar |
|---|---|---|
| `screening.domain` | Reglas puras: perfil, diff y riesgo, veredictos, capacidades, autonomía, entrevistas, reenganche | Biblioteca estándar y `pydantic` |
| `screening.application` | Casos de uso y **puertos** (`ports.py`) | `screening.domain` |
| `screening.platform` | Composition root (`container.py`) y piezas de arranque | Todo |
| `adapters` | Adaptadores de salida: persistencia, LLM, agente LangChain, canales, dobles | Todo salvo `worker` |
| `connectors` | Conectores de red hacia sistemas del cliente (MCP) | Dominio y puertos |
| `web` | Django: API, WebSocket, modelos, migraciones, comandos | Todo |
| `worker` | Bucle de trabajos y manejadores | Todo |

`import-linter` (`.importlinter`) lo hace cumplir con cuatro contratos.

## Invariantes

- **ARQ-1 · Dependencias hacia dentro.** Nada en `screening.domain` ni en
  `screening.application` importa Django, LangChain, SDKs de proveedores, `httpx`,
  `psycopg` ni `redis`. Si un contrato de imports falla, se corrige el código, no el
  contrato. Cambiar un contrato es una decisión de arquitectura y se razona aparte.
- **ARQ-2 · Puertos en la aplicación, adaptadores fuera.** Un caso de uso habla con
  el exterior solo a través de un `Protocol` de `ports.py`. Las instancias concretas se
  crean y conectan únicamente en `screening/platform/container.py`.
- **ARQ-3 · Aislamiento por cliente con RLS.** Toda tabla con datos de un cliente
  hereda de `TenantScopedModel` y su migración aplica `enable_rls`. Todo acceso a esos
  datos ocurre dentro de `tenant_context(tenant_id)`. Ningún código de aplicación usa
  el rol de base de datos que se salta RLS.
- **ARQ-4 · Sin código de cliente.** Ni nombres de clientes, ni claves de campos de su
  esquema, ni reglas de un sector concreto en `src/` o en los frontales. Lo protege
  `tests/api/test_no_tenant_code.py`; ampliar esa lista es correcto, saltársela no.
- **ARQ-5 · Historial de solo añadir.** Versiones de perfil, eventos y mensajes del
  modelo son *append-only* (`make_append_only`). No se editan ni borran filas; una
  corrección es un registro nuevo. Las excepciones legales (borrado de un candidato)
  pasan por su servicio de privacidad.
- **ARQ-6 · Publicar es un acto controlado.** El perfil llega a producción solo por
  `ProfileService.publish`: comprobaciones, diff con riesgo, puerta de evals y las
  aprobaciones que el riesgo exija. Ningún comando, migración ni seed publica por
  atajo. Una sesión conserva la versión con la que empezó.
- **ARQ-7 · Capacidades a través de la pasarela.** El agente solo actúa sobre sistemas
  del cliente mediante `CapabilityGateway`, con los permisos de su `RunContext` y el
  dial de autonomía. Ninguna herramienta llama a un conector directamente.
- **ARQ-8 · Efectos externos idempotentes.** Todo efecto fuera de la plataforma
  (mensaje, llamada, SMS, escritura en ATS o agenda) lleva clave de idempotencia y un
  estado que distingue «no enviado», «enviado» y «desconocido». Un resultado dudoso no
  se reintenta a ciegas.
- **ARQ-9 · Tiempo y dinero explícitos.** Fechas con zona horaria y guardadas en UTC;
  el «ahora» llega por el puerto `Clock`. Costes en micro-dólares enteros.
- **ARQ-10 · Nada bloquea el bucle de eventos.** En código `async`, el ORM se usa con
  sus métodos asíncronos o `sync_to_async`; el trabajo lento va al worker.

## Recomendaciones

- **ARQ-R1.** Antes de crear un módulo o un puerto nuevo, busca uno existente que
  pueda hacerse más profundo. Prefiere ampliar una interfaz pequeña a añadir otra.
- **ARQ-R2.** Mantén los nombres del [glosario](../../GLOSSARY.md) en código,
  endpoints y tablas. Un concepto, un nombre.
- **ARQ-R3.** Lógica de negocio pura (reglas, cálculos, validaciones) en el dominio,
  con datos de entrada y salida simples; los casos de uso solo orquestan.
- **ARQ-R4.** Configuración por cliente en el perfil versionado; configuración de la
  instalación en `settings.py` vía entorno. Nada de constantes de cliente en código.
- **ARQ-R5.** Una migración nueva es reversible siempre que se pueda y nunca altera
  el sentido de datos históricos.

## Señales de deuda

- Casos de uso que crecen a base de `if canal == …` o `if proveedor == …`: falta un
  puerto o un adaptador.
- Varios módulos superficiales que hay que leer juntos para entender un concepto.
- Lógica copiada entre la API, el worker y los comandos en vez de vivir en un servicio.
- Datos de cliente o decisiones de producto escritas como constantes.
- Nuevas tablas que no participan de RLS «porque no hacen falta todavía».

## Qué revisa el evaluador

- Imports nuevos que cruzan capas, aunque `import-linter` aún no los cubra.
- Modelos o consultas nuevas: herencia de `TenantScopedModel`, `enable_rls` en la
  migración, uso de `tenant_context`.
- Cualquier escritura que actualice o borre registros de solo añadir.
- Atajos de publicación o efectos externos sin idempotencia ni estado incierto.
- Lógica de un cliente o de un sector concreto en la plataforma.
- Puertos, abstracciones o parámetros sin una necesidad real (generalidad especulativa).
