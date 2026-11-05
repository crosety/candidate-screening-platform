# Convenciones

Herramientas, dependencias, estructura y estilo. `pyproject.toml`, `package.json` y
`docker-compose.yml` son la fuente de verdad de versiones e imágenes.

`LLM_MODE=openai` es el modo con inferencia real; `fake` es para desarrollo y pruebas, y `replay` describe los casetes de los tests (comparan también el cuerpo de la petición). Solo la API y el worker reciben el fichero privado de la clave del modelo.

## Herramientas de la máquina

| Herramienta | Versión | Cómo comprobarla o instalarla |
|---|---|---|
| Python | 3.12 | `python3 --version` |
| uv | 0.11.x | `uv --version` |
| Node | 24.x | `node --version` |
| pnpm | 12.10.1 | `pnpm --version`; instalación: `npm install -g pnpm@12.10.1` |
| Docker | Motor compatible con Compose | `docker info` y `docker compose version` |
| AWS CLI | 2.x, solo para acceso AWS | `aws --version` |

## Versiones exactas de las dependencias

Versiones fijadas. Cambiar una dependencia es una decisión explícita: se razona, se prueba con `make check` y se registra en el mismo cambio.

### Python (en `pyproject.toml`)

| Paquete | Versión | Para qué |
|---|---|---|
| django | 5.2.18 | Framework web (rama LTS) |
| channels | 4.3.2 | WebSocket sobre ASGI |
| channels-redis | 4.3.0 | Capa de canales con Redis |
| django-ninja | 1.7.1 | API REST con Pydantic y OpenAPI |
| uvicorn[standard] | 0.54.0 | Servidor ASGI |
| psycopg[binary] | 3.3.6 | Driver de Postgres |
| pydantic | 2.14.0 | Esquemas del dominio y de la API |
| django-environ | 0.14.0 | Lectura de `.env` |
| langchain | 1.4.4 | `create_agent` y middleware |
| langgraph | 1.2.14 | Motor de `create_agent` |
| langchain-anthropic | 1.7.6 | Integración de Claude (la usa langchain-aws) |
| langchain-aws | 1.9.0 | `ChatAnthropicBedrock` |
| anthropic | `>=1.17,<2` | SDK de Anthropic; langchain-anthropic no admite la 2.x |
| mcp | 2.3.0 | Cliente y servidor MCP (API 2.x: `MCPServer`, `Client`) |
| httpx | 0.28.1 | HTTP saliente |

Desarrollo (grupo `dev`):

| Paquete | Versión |
|---|---|
| pytest | 9.1.1 |
| pytest-django | 4.14.0 |
| pytest-asyncio | 1.4.0 |
| pytest-recording | 0.14.0 |
| vcrpy | 8.3.0 |
| ruff | 0.16.10 |
| mypy | 2.4.0 |
| django-stubs | 5.2.9 |
| import-linter | 2.15 |

### Frontales (en cada `package.json`, versiones exactas, sin `^` ni `~`)

| Paquete | Versión |
|---|---|
| react, react-dom | 19.3.0 |
| @types/react, @types/react-dom | 19.3.0 |
| @types/node | 24.13.3 |
| typescript | 5.9.3 (no uses la 6 ni la 7: openapi-typescript solo admite la 5) |
| vite | 8.3.4 |
| @vitejs/plugin-react | 6.1.2 |
| tailwindcss, @tailwindcss/vite | 4.3.3 |
| @tanstack/react-query | 5.104.1 |
| react-router | 8.4.0 |
| openapi-fetch | 0.17.0 |
| openapi-typescript | 7.13.0 |
| shadcn (CLI, solo con `pnpm dlx`) | 4.21.4 |
| @playwright/test | 1.64.0 |

Las dependencias que añada `shadcn` al instalar componentes (radix-ui, lucide-react, class-variance-authority…) se aceptan tal como las instale el CLI.

## Estructura final del repositorio


```
orbio-screening-platform/
├─ README.md · GLOSSARY.md
├─ pyproject.toml · uv.lock · .python-version
├─ .importlinter · .gitignore · .editorconfig
├─ .env.example
├─ manage.py
├─ Makefile
├─ docker-compose.yml
├─ docker/
│  ├─ python.Dockerfile
│  ├─ node.Dockerfile
│  └─ postgres/init.sql
├─ src/
│  ├─ screening/                                    núcleo: sin Django, LangChain ni I/O
│  │  ├─ domain/
│  │  ├─ application/
│  │  └─ platform/                                  composition root
│  ├─ web/                                          proyecto Django (adaptadores de entrada y persistencia)
│  │  ├─ settings.py · urls.py · asgi.py · api.py
│  │  ├─ core/                                      health, comandos de gestión, middleware
│  │  ├─ accounts/                                  usuario propio
│  │  ├─ tenancy/                                   clientes, membresías, RLS, auditoría
│  │  ├─ jobs/                                      cola del worker y enqueue()
│  │  ├─ profiles/                                  borradores, versiones, publicaciones
│  │  ├─ simulated/                                 conectores locales de sistemas del cliente (ATS, locales, agendas)
│  │  ├─ conversations/                             candidaturas, sesiones, mensajes, WebSocket
│  │  ├─ tracing/                                   llm_calls, capability_calls
│  │  ├─ decisions/                                 resultados, decisiones, outbox, bandeja
│  │  └─ evals/                                     ejecuciones y casos de evals
│  ├─ adapters/                                     adaptadores de salida
│  │  ├─ llm/                                       proveedores de modelos (OpenAI, Bedrock) y precios
│  │  ├─ agent_langchain/                           runner, middleware y herramientas
│  │  ├─ persistence/                               repositorios sobre el ORM de Django
│  │  ├─ secrets/                                   SecretStore desde el entorno
│  │  └─ fakes/                                     dobles en memoria para tests
│  ├─ connectors/                                   conectores de red: MCP (los locales viven en web/simulated)
│  └─ worker/                                       bucle y manejadores
├─ services/mcp-registro/server.py                  servidor MCP de Logística
├─ apps/
│  ├─ console/                                      consola del FDE y bandeja (React)
│  └─ chat/                                         chat del candidato (React)
├─ packages/api-client/                             cliente TypeScript generado
├─ pnpm-workspace.yaml · package.json
├─ seeds/                                           datos de semilla por cliente
├─ tests/
│  ├─ unit/ · application/ · contracts/ · api/
│  ├─ scenarios/ · live/ · cassettes/               escenarios, pruebas reales y casetes
│  └─ e2e/
└─ .github/workflows/ci.yml
```

## Capas y dependencias

Las dependencias apuntan hacia dentro. `import-linter` lo comprueba en CI con los contratos de `.importlinter`.

| Paquete | Puede importar | No puede importar |
|---|---|---|
| `screening.domain` | Biblioteca estándar y `pydantic` | Todo lo demás |
| `screening.application` | `screening.domain`, biblioteca estándar y `pydantic` | `screening.platform`, `adapters`, `connectors`, `web`, `worker`, `django`, `channels`, `ninja`, `langchain*`, `langgraph`, `anthropic`, `boto3`, `mcp`, `httpx`, `psycopg`, `redis` |
| `connectors` | `screening.domain`, `screening.application` (solo los puertos), `httpx`, `mcp` y biblioteca estándar | `web`, `worker`, `adapters`, `django`, `langchain*` |
| `adapters` | Todo menos `worker` | `worker` |
| `web` | Todo | — |
| `worker` | Todo | — |
| `screening.platform` | Todo: es el composition root | — |

El dominio y los casos de uso permanecen independientes de Django y LangChain;
la composición de adaptadores corresponde a `screening/platform`.

## Estilo de código

- Python 3.12 con anotaciones de tipo en todas las funciones. `ruff` y `mypy` configurados en `pyproject.toml`.
- Identificadores en inglés. Textos para personas en español; el chat del candidato y sus avisos, también en inglés. Excepciones de documentación en `AGENTS.md`.
- **Modelos de Django:**
  - Clave primaria `id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)`.
  - `created_at = models.DateTimeField(auto_now_add=True)` en todos; `updated_at` solo donde hace falta.
  - Nombres de tabla: los que pone Django por defecto (`<app>_<modelo>`).
- Fechas siempre con zona horaria (`USE_TZ = True`), guardadas en UTC.
- Dinero: costes en micro-dólares enteros (`cost_usd_micros`, `BigIntegerField`). 1 $ = 1.000.000.
- **Errores de la API:** con `ninja.errors.HttpError(status, "mensaje en español")`.
  - Códigos: 400 entrada no válida, 401 sin sesión, 403 sin permiso, 404 no existe o no es de tu cliente, 409 conflicto de versión (`rev`).
- **Logs:** módulo `logging` de Python, con el logger `logging.getLogger(__name__)`. Nunca registres textos de candidatos completos, contraseñas ni tokens.
- **Asincronía:** el código del WebSocket y del agente es `async`. El ORM se llama con `sync_to_async` o con los métodos asíncronos de Django (`aget`, `acreate`, `afirst`…). Nunca bloquees el bucle de eventos.

## Variables de entorno

Todas se leen en `web/settings.py` con `django-environ`. `.env.example` lleva solo valores de ejemplo.

| Variable | Ejemplo | Uso |
|---|---|---|
| `DJANGO_SECRET_KEY` | `cambia-esto` | Clave de Django |
| `DJANGO_DEBUG` | `true` | Modo depuración (solo local) |
| `DATABASE_URL` | `postgres://screening:screening@localhost:5432/screening` | Postgres con el rol de la aplicación |
| `REDIS_URL` | `redis://localhost:6379/0` | Capa de canales |
| `AWS_PROFILE` | `(perfil local)` | Perfil de AWS para Bedrock o para resolver secretos en AWS |
| `AWS_REGION` | `eu-west-1` | Región desde la que se llama a Bedrock |
| `SEED_PASSWORD` | `(valor local)` | Contraseña de los usuarios locales que crea `make seed` |
| `INGEST_TOKEN` | `dev-ingest-token` | Token de la ingesta de candidaturas del ATS |
| `CHAT_BASE_URL` | `http://localhost:5174` | Para construir el enlace del candidato |
| `SEEDS_DIR` | `seeds` | Carpeta de semillas |
| `LLM_MODE` | `openai` | `openai` (proveedor activo), `bedrock` (alternativo), `replay` (casetes) o `fake` (dobles) |
| `OPENAI_API_KEY_FILE` | `(ruta local privada)` | Fichero con la clave de OpenAI, montado como secreto en `api` y `worker` |
| `GPT_LIVE_ENABLED` | `false` | Activa la voz alternativa con GPT-Live en el navegador |
| `GPT_LIVE_VOICE_ID` | `(vacío)` | Voz que usa GPT-Live cuando está activo |
| `PUBLISH_REQUIRES_EVALS` | `true` | Publicar exige evals aprobadas del borrador |

## Makefile

Objetivos principales del `Makefile`.

| Objetivo | Qué hace |
|---|---|
| `make up` | Levanta los 7 servicios con `docker compose up -d --build` y espera a que estén sanos |
| `make down` | Para los servicios |
| `make logs` | Sigue los logs |
| `make migrate` | `manage.py migrate` dentro del contenedor `api` |
| `make seed` | `manage.py seed_local` y el ejemplo de candidaturas dentro del contenedor `api` |
| `make check` | `lint` + `typecheck` + `imports` + `test` + `fe-typecheck` + comprobación de migraciones |
| `make lint` | `ruff check` y `ruff format --check` |
| `make fmt` | `ruff format` y `ruff check --fix` |
| `make typecheck` | `mypy` |
| `make imports` | `lint-imports` |
| `make test` | `pytest` (necesita Postgres y Redis levantados) |
| `make fe-typecheck` | `pnpm -r typecheck` |
| `make openapi` | Exporta el OpenAPI a `packages/api-client/openapi.json` |
| `make api-client` | `make openapi` y genera `packages/api-client/src/schema.d.ts` |
| `make bedrock-smoke` · `make openai-smoke` | Llamada real mínima al proveedor de modelos |
| `make record T=…` | Graba un casete con el modelo real; requiere autorización y tope de gasto |
| `make e2e` | Pruebas de extremo a extremo con Playwright en modo `fake` |

## Tests

- **Ubicación:**
  - `tests/unit/`: dominio puro, sin base de datos.
  - `tests/application/`: casos de uso con dobles de `adapters/fakes`.
  - `tests/contracts/`: adaptadores y conectores contra su puerto.
  - `tests/api/`: endpoints con la base de datos de pruebas.
  - `tests/e2e/`: Playwright.
- Los que tocan la base de datos llevan `@pytest.mark.django_db`. Usa `@pytest.mark.django_db(transaction=True)` si prueban RLS, el worker, el WebSocket o cualquier repositorio o servicio asíncrono: `sync_to_async` usa otra conexión, que no ve la transacción del test.
- Nunca llaman a la red, salvo a Postgres y Redis locales.
- **El modelo en los tests:**
  - `LLM_MODE` solo cambia las piezas que llaman al modelo (conversador, guardián, evaluador, simulador, juez y copiloto). La persistencia es siempre Django, en todos los modos.
  - Los tests de `unit`, `application` y `contracts` construyen sus servicios con dobles de `adapters/fakes`. Los de `api` usan `get_container()` con `LLM_MODE=fake`, que fija un fixture `autouse` de `tests/conftest.py`.
  - Los tests del adaptador de LangChain usan casetes de `pytest-recording`, en `tests/cassettes/`, y se ejecutan con `--record-mode=none`.
  - Grabar un casete cuesta dinero: solo se hace con autorización de la persona responsable y tope de gasto, con `make record`.
- Marca `@pytest.mark.live` en cualquier test que llame al modelo real. No se ejecutan nunca en `make test` ni en CI.

## Base de datos

Postgres incluye pgvector **0.8.2** mediante la imagen oficial del proyecto `pgvector/pgvector:0.8.2-pg16-bookworm`, índice verificado `sha256:00ba258a66dac104fd5171074a0084462a64a1369d8513f3d0a634e2f24d15bc`. Postgres 16. No hace falta paquete Python adicional: SQL parametrizado sobre psycopg. Al migrar desde una imagen Alpine, usa un volumen nuevo y restaura desde copia; no reutilices su directorio de datos (otra libc). Embeddings `text-embedding-3-small`, 1536 dimensiones, API oficial y coste trazado, nunca en tests/CI. Fuentes: [pgvector](https://github.com/pgvector/pgvector), [embeddings](https://developers.openai.com/api/docs/guides/embeddings).
