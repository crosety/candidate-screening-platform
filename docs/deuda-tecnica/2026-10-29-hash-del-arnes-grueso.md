# El hash del arnés invalida las evals ante cambios cosméticos

- **Detectada:** 2026-10-29, por el evaluador de la guía de agentes e IA
- **Guía y regla:** agentes e IA · IA-6, IA-9; señal «evals que se dejan de ejecutar porque son caras»
- **Severidad:** baja
- **Estado:** abierta

## Qué es
La puerta de publicación exige que la ejecución de evals tenga el mismo hash de arnés
que el código actual (`src/adapters/eval_harness.py`). El hash cubre todo
`src/screening`, `src/connectors` y otros ficheros del runtime: renombrar una
plantilla o cambiar un mensaje de error invalida todas las suites aprobadas.

## Cómo se ha investigado
Lectura de `RUNTIME` en `eval_harness.py` y de `run_passes` en
`src/adapters/persistence/evals_gate.py`. Tras los cambios del 29/10, la próxima
publicación de cualquier oferta necesita una ejecución de evals nueva. La suite
Terra de 16 personas medida el 31/10 costó 1,652120 USD; no se debe extrapolar el
coste del runtime anterior.

## Por qué importa
Encarece cambios menores y empuja a agrupar o retrasar publicaciones.

## Opciones
1. Calcular el hash sobre los ficheros que de verdad dan forma a la conducta del agente
   (prompts, herramientas, capacidades, evaluador, juez, simulador). Esfuerzo bajo,
   pero hay que razonar bien qué queda fuera.
2. Mantenerlo grueso como red de seguridad y aceptar el coste.

## Recomendación
Mantener la comprobación estricta actual y delimitar el hash en un cambio dedicado,
con pruebas que demuestren qué cambios de conducta invalidan las evals. El coste
observado con Terra ya justifica revisar el alcance, no saltarse la comprobación.
