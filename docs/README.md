# Documentación

## Funcional

| Documento | Qué cubre |
|---|---|
| [Producto](producto.md) | Actores, recorrido del candidato, veredictos, canales, selección, configuración y publicación |
| [Configuración de ofertas](configuracion-ofertas.md) | Empresa, campos, encaje, biblioteca, contexto efectivo y preparación de borradores anteriores |
| [Diseño del proceso](diseno-proceso.md) · [PDF](../output/pdf/diseno-proceso-grupo-sazon.pdf) · [en inglés](process-design.md) · [PDF en inglés](../output/pdf/process-design-grupo-sazon.pdf) | Configuración de Grupo Sazón: siete datos, preguntas, validaciones, personalidad, resultados y casos límite. Versiones en español y en inglés de la misma configuración; los PDF se exportan con `tools/export_process_pdf.py` |
| [Conversaciones de ejemplo](example-conversations.md) | En inglés: una conversación real con el modelo y su JSON, y tres del seed (propuesta de descarte, derivación y abandono) |
| [Reenganche, señales e idiomas](reenganche-y-tono.md) | Recordatorios por silencio, abandono, idioma y tono por turno |
| [Voz en el navegador](voz.md) | «Hablar aquí»: funcionamiento, configuración y comprobación de calidad |
| [Agenda](agenda.md) | Reserva, cambio y cancelación de entrevistas |
| [FAQ con búsqueda](faq-rag.md) | Conocimiento del cliente y cómo lo usa el agente |
| [Integración ATS](integracion-ats.md) · [OpenAPI](ats/openapi.yaml) | Contrato con el sistema de seguimiento de candidatos |
| [Privacidad y operación](privacidad-operacion.md) | Datos, retención, retirada y operación diaria |

## Técnica y operación

| Documento | Qué cubre |
|---|---|
| [Arquitectura](arquitectura.md) | Capas, modelo de datos, RLS, agentes, API, WebSocket y worker |
| [Convenciones](convenciones.md) | Herramientas, versiones, estructura, estilo y comandos |
| [Inferencia](openai-runtime.md) | Proveedor de modelos, papeles, esfuerzo y secretos |
| [Métricas de modelos](metricas-llm.md) | Exportación privada a Langfuse, entrega, voz y conciliación |
| [Despliegue y escalado](despliegue-y-escalado.md) | Capacidad, coste, despliegue y monitorización |
| [Producción](produccion.md) | Requisitos para un entorno productivo |
| [Canales con Twilio](twilio.md) | WhatsApp, llamadas y SMS: configuración, exposición y fallos |

## Referencia

- [Glosario](../GLOSSARY.md): términos del producto.
- [Decisiones de arquitectura](adr/README.md): criterios de diseño y control por canal.
- [Instalación y seeding](../README.md#quick-start): preparar el entorno y recorrer el producto (README en inglés).
