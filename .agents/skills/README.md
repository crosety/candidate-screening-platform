# Skills de agentes

Skills que usan los agentes de programación en este repositorio (Codex lee
`.agents/skills/`; Claude Code, `.claude/skills/`, que enlaza aquí). Flujo
recomendado y reglas: [`AGENTS.md`](../../AGENTS.md).

## Propios

| Skill | Para qué |
|---|---|
| `evaluar-cambios` | Un evaluador independiente por cada guía de `docs/guias/`; obligatorio antes de dar un trabajo por hecho |

## De Matt Pocock

Copiados de [mattpocock/skills](https://github.com/mattpocock/skills), commit
`59db609eb1e18aaf59b63284d0e52255aff0c63c` (27/10/2026), licencia MIT
([`LICENSE-mattpocock-skills`](LICENSE-mattpocock-skills)). Se actualizan a mano
volviendo a copiar desde ese repositorio y reaplicando las adaptaciones.

| Skill | Para qué | Adaptación |
|---|---|---|
| `grill-me`, `grilling` | Entrevista para alinear un plan antes de empezar | — |
| `grill-with-docs` | Igual, actualizando glosario y ADR | — |
| `domain-modeling` | Mantener `GLOSSARY.md` y `docs/adr/` | — |
| `codebase-design` | Vocabulario de módulos profundos, interfaces y costuras | — |
| `improve-codebase-architecture` | Buscar oportunidades de profundizar módulos | Respeta los invariantes de `docs/guias/arquitectura.md` y registra deuda |
| `tdd` | Ciclo rojo-verde | Costuras de `docs/guias/testing.md`; revisión con `evaluar-cambios` |
| `diagnosing-bugs` | Bucle disciplinado de diagnóstico | — |
| `to-spec` | Convertir una conversación en especificación | Tracker local en `.scratch/` con línea `Status:` |
| `implement` | Implementar una especificación | Guías, `make check`, `evaluar-cambios`, deuda y commits según `AGENTS.md` |
| `research` | Investigar con fuentes primarias | — |
| `handoff` | Documento de traspaso a otro agente | — |

No se instalan `code-review` (lo sustituye `evaluar-cambios`, que generaliza su eje
de estándares a una guía por evaluador), `setup-matt-pocock-skills` (su resultado ya
está en `docs/agents/` y en `AGENTS.md`), `triage`, `to-tickets`, `wayfinder` ni los
skills de escritura o cursos, que no encajan en el flujo de este repositorio.
