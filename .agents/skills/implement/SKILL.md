---
name: implement
description: "Implement a piece of work based on a spec or set of tickets."
disable-model-invocation: true
---

<!-- Adapted for this repository: evaluators per guide instead of code-review; commits follow AGENTS.md. -->

Implement the work described by the user in the spec or tickets.

If the user passes a ticket reference, fetch it from the issue tracker (`docs/agents/issue-tracker.md`) and state its title before starting. If the reference is ambiguous, ask.

Before writing code, read the guides in `docs/guias/` that apply to the area you touch, and `GLOSSARY.md`.

Call the Skill tool with "tdd" where possible, at the seams defined in `docs/guias/testing.md`.

Run typechecking regularly, single test files regularly, and `make check` once at the end.

Once done, call the Skill tool with "evaluar-cambios": one independent evaluator per guide. Fix, justify or record as debt every finding, and tell the user about any technical debt reported.

Update the functional documentation in the same change when observable behaviour changes.

Commit only as `AGENTS.md` allows. Never push.
