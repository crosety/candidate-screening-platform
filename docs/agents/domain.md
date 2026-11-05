# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root (single-context repo; written in Spanish).
- **`docs/adr/`**: read ADRs that touch the area you're about to work in.
- **`docs/guias/`**: the engineering guides. Their rules are what the guide evaluators check (`evaluar-cambios` skill).
- **`docs/producto.md`** and the functional docs listed in `docs/README.md` for current product behaviour.

If an ADR folder is empty, **proceed silently**. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates ADRs lazily when decisions actually get resolved.

## File structure

```
/
├── GLOSSARY.md
├── docs/
│   ├── adr/            ← architecture decision records (0001-<slug>.md)
│   ├── guias/          ← engineering guides, one evaluator each
│   └── deuda-tecnica/  ← technical debt register
└── src/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `GLOSSARY.md`. Code identifiers are in English; when the glossary gives the English identifier (for example `Session`, `result_schema`), use it in code.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (add it to `GLOSSARY.md` in the same change).

## Flag ADR and guide conflicts

If your output contradicts an existing ADR or a guide invariant, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_
