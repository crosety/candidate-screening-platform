# Voz directa y control de acciones

- **Estado:** aceptada por la persona responsable el 29/10/2026, condicionada a la
  aceptación adversarial de voz antes de abrirla a candidatos reales.

La conversación hablada usa un agente de voz directo en lugar de transcribir cada
turno para invocar después al conversador de texto. Se prioriza la fluidez; ambos
canales comparten contexto de candidatura, historial, perfil congelado y capacidades,
pero no ofrecen la misma garantía de revisión del contenido.

## Controles y límite

En texto, el guardián revisa la respuesta antes de enviarla. En voz, Jev revisa cada
herramienta antes de ejecutarla y deriva las acciones de riesgo al guardián principal.
Los permisos, la autonomía, el aislamiento, la evidencia y la intervención humana
siguen siendo controles de plataforma, independientes del modelo de voz.

El audio no se retiene hasta validar cada frase. Por tanto, una respuesta hablada
inadecuada podría oírse aunque la acción correspondiente quede bloqueada. Por eso
se distinguen la revisión de texto y el control de acciones en voz.

## Operación

La aceptación con candidatos sintéticos debe comprobar introducción, continuidad,
correcciones, inyección, privacidad, herramientas y toma humana. La habilitación para
candidatos reales requiere evaluar además retención, controles de contenido del
proveedor y el riesgo residual por despliegue. Una batería superada no certifica
ausencia de respuestas inadecuadas ni autoriza por sí sola producción.

Retener y validar todo el audio antes de reproducirlo sigue siendo una alternativa,
pero añade latencia y cambia la arquitectura conversacional; no se simula esa
garantía con un aviso o una validación posterior.
