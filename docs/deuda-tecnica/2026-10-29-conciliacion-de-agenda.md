# Recuperación de una reserva de agenda incierta

- **Detectada:** 2026-10-29, revisión independiente de arquitectura
- **Guía y regla:** arquitectura · ARQ-8
- **Severidad:** media
- **Estado:** abierta

## Qué es
`InterviewService._book` escribe en el calendario antes de crear la reserva local.
Si el calendario aplica la operación y agota el timeout al responder, devuelve
`unavailable` sin fila de entrevista. La clave idempotente permite recuperarla al
repetir el mismo hueco, pero no existe reconciliación autónoma de esa intención.

## Cómo se ha investigado
Lectura de `screening/application/interviews.py` y de
`tests/application/test_interviews.py::test_ambiguous_timeout_then_retry_books_once`.
El test comprueba la escritura aplicada y la recuperación por repetición. Elegir otro
hueco no reconstruye la reserva ausente; cambio y cancelación requieren la fila local.

## Por qué importa
Una reserva puede ocupar un hueco sin verse en la agenda ni generar recordatorio.
La idempotencia evita duplicarla, pero no garantiza la reconciliación.

## Opciones
Persistir la intención antes de llamar al calendario, guardar el resultado incierto
y conciliar con la misma clave antes de admitir otra acción. Hace falta extender el
puerto, persistencia, worker y contrato del conector; esfuerzo medio.

## Recomendación
Cerrar antes de conectar una agenda externa. El conector de esta instalación es
local y la limitación está documentada; no afirmar tolerancia completa a respuestas
perdidas por disponer únicamente de claves idempotentes.
