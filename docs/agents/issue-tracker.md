# Issue tracker: Local Markdown

Issues and specs for this repo live as markdown files in `.scratch/` (ignored by git;
the repository has no shared tracker).

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`, never a single combined tickets file
- Status is recorded as a `Status:` line near the top of each issue file: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human` or `wontfix`
- Comments and conversation history append to the bottom of the file under a `## Comments` heading
- Guide evaluations live in `.scratch/evaluaciones/` (see the `evaluar-cambios` skill)

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<feature-slug>/` (creating the directory if needed).

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or the issue number directly.

Specs and tickets are working notes: anything that changes product behaviour must also
end up in the functional documentation (`docs/guias/documentacion-funcional.md`).
