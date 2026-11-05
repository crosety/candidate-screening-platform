---
name: evaluar-cambios
description: "Revisa los cambios con un evaluador independiente por cada guía de docs/guias (arquitectura, testing, seguridad, agentes e IA, interfaz, documentación funcional), en paralelo y con contexto limpio. Recoge incumplimientos, riesgos y deuda técnica, tiene en cuenta las notas de quien implementa y obliga a informar de la deuda. Úsalo siempre antes de dar un trabajo por terminado o de abrir una pull request, y cuando pidan revisar una rama o unos cambios."
---

# Evaluar cambios contra las guías

Cada guía de `docs/guias/` (todas salvo `README.md`) tiene un evaluador. Los
evaluadores son **independientes**: no comparten contexto entre sí ni con quien
implementó, y solo buscan problemas. Sus resultados son avisos razonados; quien
implementa decide, pero no puede ignorarlos en silencio.

## 1. Fijar el alcance

- **Base**: la que indique la persona; si no, `git merge-base HEAD main`. Si no hay
  `main`, la rama de la que salió el trabajo o el último commit publicado. Comprueba
  que resuelve (`git rev-parse <base>`).
- **Cambios**: `git diff <base>` (incluye lo no confirmado) más los ficheros nuevos sin
  seguimiento (`git ls-files --others --exclude-standard`). Si el diff está vacío,
  para aquí: no hay nada que evaluar.
- Crea la carpeta de la ejecución `.scratch/evaluaciones/<AAAAMMDD-HHMM>-<rama>/`
  (ignorada por git) y deja en ella el material que leerán los evaluadores, para que
  no necesiten ejecutar comandos:
  - `cambios.diff`: `git diff <base> -- . ':!.env*' ':!.runtime-secrets'`;
  - `ficheros-nuevos.txt`: los ficheros sin seguimiento, uno por línea;
  - `commits.txt`: `git log --oneline <base>..HEAD`.

## 2. Escribir las notas para los evaluadores

Antes de lanzarlos, escribe `notas.md` en esa carpeta. Es la única forma de que un
evaluador sepa lo que tú sabes:

```markdown
# Notas de quien implementa
## Objetivo del cambio
## Decisiones que se apartan a propósito de una guía
- <ID de regla>: qué se hace distinto, por qué y qué lo compensa.
## Riesgos que ya conozco
## Qué he probado (comandos y resultado)
## Fuera de alcance
```

Sé honesto: una excepción no declarada que el evaluador encuentre cuenta como
incumplimiento; una declarada y razonada puede aceptarse.

## 3. Lanzar un evaluador por guía

- Descubre las guías: `ls docs/guias/*.md`, sin `README.md`.
- Lanza **todos en paralelo**, cada uno como subagente nuevo. En Claude Code usa el
  tipo `evaluador-guia` (`.claude/agents/evaluador-guia.md`): solo lee ficheros y
  escribe su informe, sin terminal. En otro entorno, un subagente con las mismas
  limitaciones; si solo dispone de terminal, que la use únicamente para leer.
- Prompt de cada uno: el contenido de [`evaluador.md`](evaluador.md) con sus huecos
  rellenos (guía, carpeta de la ejecución y ruta de salida). No le
  pases tus conclusiones ni el resultado de otros evaluadores.
- Cada evaluador escribe su informe en `<carpeta>/<guía>.md` y lo devuelve.

## 4. Agregar sin reordenar

Presenta los informes por guía, uno tras otro, sin mezclarlos ni elegir un «peor
hallazgo» global: cada guía mide algo distinto. Para cada hallazgo, decide y deja
constancia en `<carpeta>/respuestas.md`:

| Decisión | Cuándo |
|---|---|
| **Corregir** | Es un problema real. Corrige y vuelve a lanzar solo los evaluadores de las guías afectadas (como máximo dos rondas). |
| **Justificar** | Es una excepción deliberada. Escribe por qué y qué la compensa; si es un invariante (`XXX-n`), la persona responsable tiene que aceptarla. |
| **Aceptar como deuda** | Es real pero no se aborda ahora. Pasa al paso 5. |
| **Rechazar** | El evaluador se equivoca. Explica con evidencia (fichero, línea, prueba) por qué. |

Nunca des por terminado un trabajo con un hallazgo `bloqueante` sin corregir ni
justificar ante la persona responsable.

## 5. Deuda técnica: investigar, registrar y avisar

Cuando un evaluador devuelva deuda (del diff o acumulada en el código que toca), o
un problema que no está en ninguna guía:

1. **Investígala tú.** Verifica que existe, mide su alcance (ficheros, pruebas
   afectadas, frecuencia de cambio) y estima el coste de abordarla ahora o después.
2. **Regístrala** en `docs/deuda-tecnica/` con la plantilla de
   [`docs/deuda-tecnica/README.md`](../../../docs/deuda-tecnica/README.md), en un
   fichero `AAAA-MM-DD-<tema>.md`, y añádela al índice. Si ya existe una entrada del
   mismo tema, actualízala en vez de duplicarla.
3. **Avisa a la persona** en tu mensaje final, con un resumen y el enlace, aunque no
   te lo haya preguntado.

Si un evaluador detecta algo que ninguna guía recoge y se repite, propón ampliar la
guía correspondiente en el mismo aviso.

## 6. Cerrar

Resume para la persona responsable: evaluadores ejecutados, hallazgos por guía y qué
se hizo con cada uno, excepciones pendientes de su aceptación y deuda registrada.
Los informes completos quedan en `.scratch/evaluaciones/`.
