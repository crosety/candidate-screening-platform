# Guía · Testing

**Propósito.** Que cada cambio llegue con la prueba que de verdad lo protege, en el
nivel adecuado, sin depender de la red ni de un modelo real. **Alcance:** todo
cambio de comportamiento, y cualquier cambio en `tests/`, `apps/*/tests/`, casetes,
personas de evals o en la configuración de pytest.

## Filosofía

Probamos **comportamiento a través de interfaces públicas**, no implementación. Una
prueba buena se lee como una especificación («un candidato sin carnet queda en
revisión sin descarte automático») y sobrevive a un refactor que no cambia el
comportamiento. Por eso la mayoría de nuestras pruebas son **de integración**: casos
de uso con dobles en los bordes, API contra Postgres real con RLS, adaptadores contra
su contrato. Las pruebas unitarias se reservan para reglas puras del dominio.

El comportamiento del agente no se demuestra con tests deterministas: se mide con
**evals** sobre el modelo real y protege la publicación.

## Niveles

| Nivel | Dónde | Qué prueba | Con qué |
|---|---|---|---|
| Dominio | `tests/unit/` | Reglas puras: validación de campos, diff y riesgo, autonomía, fechas, citas literales | Tablas de casos; sin base de datos |
| Aplicación | `tests/application/` | Casos de uso completos por sus puertos | Dobles de `adapters/fakes` y repositorios en memoria |
| Contratos | `tests/contracts/` | Un adaptador cumple su puerto: LangChain, conectores, especificación ATS | Casetes grabados (`--record-mode=none`), conectores locales |
| API e integración | `tests/api/` | Endpoints, worker, WebSocket, RLS, comandos | Postgres y Redis locales, `LLM_MODE=fake` |
| Frontales | `apps/chat/tests/` | Lógica de interfaz pura: idioma, errores visibles, estados de canal | `node --test`, dentro de `make fe-typecheck` |
| Escenarios | `tests/scenarios/` | Conversaciones y evaluaciones completas reutilizadas por contratos y pruebas reales | Casetes o modelo real según quién los llame |
| Pruebas reales | `tests/live/` | Grabar o comprobar casetes contra el proveedor | `@pytest.mark.live`, solo con `make record` |
| Extremo a extremo | `tests/e2e/` | Recorridos críticos en el navegador: alta, chat, derivación, consola, publicación | Playwright contra la pila aislada (`make e2e`), modelo falso |
| Evals | `run_evals` | Comportamiento conversacional por oferta con personas sintéticas | Modelo real, juez, puerta de publicación |
| Smoke | `make openai-smoke`, `/health` | Que la instalación y las credenciales funcionan | Una llamada real mínima |

## Invariantes

- **TST-1 · Todo cambio de comportamiento trae su prueba** en el nivel más alto que
  lo observe de forma fiable. Un bug corregido llega con la prueba que fallaba antes.
- **TST-2 · `make test` y la CI nunca llaman a servicios reales**: ni modelos ni
  proveedores de mensajería o voz. Solo Postgres y Redis locales. Las pruebas que
  llaman a un modelo llevan `@pytest.mark.live` y solo se ejecutan con `make record`.
- **TST-3 · Dobles solo en los bordes.** Se sustituyen sistemas externos (modelo,
  proveedores, reloj, red) por los dobles de `adapters/fakes` o por casetes. No se
  mockean módulos propios ni colaboradores internos.
- **TST-4 · Las expectativas vienen de una fuente independiente**: un literal
  conocido, un ejemplo resuelto o la especificación. Nunca se recalcula el valor
  esperado como lo hace el código.
- **TST-5 · RLS y concurrencia se prueban con la base de datos real**:
  `django_db(transaction=True)` en pruebas de RLS, worker, WebSocket o servicios
  asíncronos. Un dato de otro cliente nunca debe ser visible.
- **TST-6 · No se desactiva ni se debilita una prueba** para que algo pase: nada de
  `skip`, `xfail`, tolerancias infladas, `# type: ignore` o `# noqa` sin un
  comentario que explique la causa real.
- **TST-7 · Casetes coherentes con el código.** Si cambia un prompt, una herramienta
  o el cuerpo de una petición grabada, se vuelve a grabar el casete afectado (coste
  mínimo y autorizado) en lugar de editarlo a mano.
- **TST-8 · Cambios en el agente pasan por evals.** Cambiar prompts, herramientas,
  guardián, evaluador, esquema del resultado o el arnés exige una ejecución de evals
  de cada oferta abierta antes de publicar (véase [Agentes e IA](agentes-e-ia.md)).
- **TST-9 · `make check` en verde** antes de dar un trabajo por terminado: lint,
  formato, tipos, contratos de imports, migraciones, pytest, tipos de frontales y sus
  pruebas (`node --test`). Los e2e se ejecutan con `make e2e` cuando el cambio toca la
  interfaz o un recorrido, y siempre en la CI.

## Recomendaciones

- **TST-R1.** Escribe primero la prueba que falla y después lo mínimo para pasarla
  (skill `tdd`). Una costura nueva de test se acuerda antes de usarla.
- **TST-R2.** Nombra la prueba por el comportamiento, en el idioma del dominio
  (`test_an_interview_belongs_only_to_a_qualified_candidate`).
- **TST-R3.** Una idea por prueba. Si necesitas muchas aserciones heterogéneas, son
  varias pruebas o falta un caso de uso.
- **TST-R4.** Para cada persona nueva de evals, define veredicto esperado y
  comprobaciones `must`/`must_not` verificables en la conversación; los hechos de la
  persona deben ser coherentes con ese veredicto.
- **TST-R5.** Las pruebas de API que necesitan base de datos aislada usan su propio
  nombre de base cuando se ejecutan en paralelo con otro trabajo.
- **TST-R7.** Comprueba el resultado por la interfaz pública cuando exista. Leer la
  base de datos en una prueba de API está justificado cuando el comportamiento *es*
  la persistencia o su aislamiento: RLS, registros de solo añadir, outbox, eventos de
  auditoría o efectos que ningún endpoint expone.
- **TST-R6.** Un e2e nuevo solo para un recorrido crítico que no cubra un nivel
  inferior; son lentos y caros de mantener.

## Señales de deuda

- Pruebas que se rompen en refactors sin cambio de comportamiento.
- Dobles cada vez más listos que replican lógica de producción.
- Fixtures gigantes compartidas que nadie se atreve a tocar.
- Casetes o fixtures congeladas que ya no representan el producto actual.
- Funcionalidad visible sin ninguna prueba por encima del dominio.
- Evals con personas que siempre pasan o cuyo veredicto no se justifica por sus hechos.

## Qué revisa el evaluador

- Comportamiento nuevo o cambiado sin prueba, o con la prueba en el nivel equivocado.
- Mocks de módulos propios, aserciones sobre llamadas internas o pruebas tautológicas.
- Pruebas que tocarían la red o un modelo real fuera de `live`.
- Pruebas desactivadas, debilitadas o con tolerancias que esconden un fallo.
- Prompts, herramientas o peticiones grabadas cambiados sin regrabar casetes ni medir
  con evals.
- Que el trabajo diga haber ejecutado `make check` (o explique por qué no).
