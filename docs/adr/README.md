# Registros de decisiones (ADR)

Decisiones de diseño que explican la elección entre alternativas y sus consecuencias
en el producto.

Ficheros `NNNN-<tema>.md`, numerados en orden. Una decisión que se sustituye no se
borra: la nueva la referencia.

- [Voz directa y control de acciones](0001-voz-directa-y-control-de-acciones.md):
  garantías distintas por canal, herramientas bloqueadas antes de actuar y riesgo
  residual del audio.
- [Alojamiento gestionado por Orbio](0002-alojamiento-gestionado-por-orbio.md):
  AWS operado por Orbio, plataforma compartida por región y despliegues dedicados.

Las decisiones de base de la plataforma (Django con Channels y Ninja, capas con
puertos y adaptadores, RLS desde el inicio, cola propia en Postgres, versiones
inmutables, puerta de evals, casetes en lugar de modelos reales en CI) están
descritas en [arquitectura](../arquitectura.md).
