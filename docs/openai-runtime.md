# Inferencia con OpenAI

El proveedor activo es OpenAI sobre la API Responses (`LLM_MODE=openai`).
El conversador de texto usa **`gpt-6.1-terra` con esfuerzo `low`**.
Los demás papeles de OpenAI usan **`gpt-6.1-terra` con esfuerzo `medium`**:
evaluador, guardián de respaldo de Jev, juez, simulador, copiloto FDE, asistente del
reclutador y clasificador de señales. Jev y el motor de voz directa no cambian.
Cada papel de agente tiene su esfuerzo de razonamiento y su
límite de salida en `adapters/llm/openai.py`. Los puertos de aplicación, las
herramientas, el guardián, la persistencia y los tests offline no dependen del
proveedor. El adaptador de Bedrock (`LLM_MODE=bedrock`) sigue disponible como
alternativa, pero no es el proveedor activo.

## Arranque seguro

En `.env` (ignorado por Git): `LLM_MODE=openai` y la clave como referencia, nunca
como valor. Hay dos formas admitidas:

- `OPENAI_API_KEY_FILE`, un fichero local privado que Docker monta como secreto solo
  en los contenedores `api` y `worker`. El lanzador comprueba que existe y no está
  vacío, lo conserva y entrega a Compose su ruta absoluta. Tiene precedencia sobre
  `OPENAI_API_KEY` si ambas variables están presentes.
- Una referencia a AWS Secrets Manager
  (`{{resolve:secretsmanager:…:SecretString:OPENAI_API_KEY:AWSCURRENT}}`) con
  `AWS_PROFILE`, `AWS_REGION` y `ASM_EXEC_BIN` apuntando al lanzador `asm-exec`.
  `make up` resuelve la referencia al arrancar e inyecta el valor solo en `api` y
  `worker`. La materialización privada usa permisos `0600`; un arranque posterior
  en modo `fake` no borra una clave ya provisionada.

No ejecutes `docker compose config` ni inspecciones variables de entorno con
credenciales. `make openai-smoke` hace una petición breve y solo muestra modelo,
respuesta y uso.

La API se llama con `store=False`: el historial lo conserva la plataforma, incluidos
los bloques opacos de razonamiento cifrado que necesitan las herramientas. Esto no
equivale a un acuerdo de retención cero con el proveedor; ese acuerdo depende de la
cuenta de cada despliegue.

## Pruebas y coste

`make check` no llama a OpenAI ni resuelve secretos. Las pruebas reales y sus casetes
se ejecutan expresamente (`make record`), nunca en CI, con autorización y tope de
gasto. Cada llamada queda trazada con tokens y coste estimado según la tabla de
`adapters/llm/pricing.py`. Tarifas Standard de contexto corto, por millón de tokens:

| Modelo | Entrada | Salida | Lectura de caché | Escritura 30 min |
|---|---:|---:|---:|---:|
| GPT-6.1 Terra | 1,00 USD | 5,00 USD | 0,05 USD | 1,25 USD |
| GPT-6 Luna (histórico) | 0,10 USD | 0,50 USD | 0,01 USD | 0,125 USD |

Son estimaciones, no una factura. Se aplican timeout, reintentos y un
límite de seis llamadas del agente por turno.

Los escenarios grabados de conversación y evaluación fijan el modelo y los esfuerzos
con los que se grabaron (`tests/scenarios/legacy_openai.py`), para que su reproducción
sea determinista. Las pruebas de contrato comprueban el modelo, esfuerzo y metadatos
del runtime actual; las pruebas con el modelo real se ejecutan aparte y necesitan
presupuesto autorizado.

La selección de modelo es global en el runtime, no forma parte de la versión
congelada del perfil: también afecta al retomar sesiones existentes. El 04/11/2026
se completó con el runtime actual una conversación real en inglés (alta, local
propuesto, fecha relativa, resumen, evaluación y reserva de entrevista), con
conversador, guardián, señales y evaluador en Terra: **0,0549 USD** trazados.

## Elección del modelo y coste por candidato

**Criterio de partida, no validación.** Artificial Analysis Intelligence Index
v5.0.1, fichas consultadas el 04/11/2026 ([artificialanalysis.ai](https://artificialanalysis.ai/)):
Terra `medium` obtiene 38 puntos con 0,17 USD y 2,6 minutos por tarea, en la frontera
de Pareto de inteligencia frente a coste por tarea y frente a tiempo por tarea. Terra
`low` obtiene 29 con 0,07 USD y 1,0 minuto, el más barato y rápido de la comparación.
La validación propia es la puerta de evals de cada oferta antes de publicar.

| Cifra | Valor | Naturaleza |
|---|---|---|
| Coste por candidato, Terra | 0,03–0,06 USD | Estimación. Extremo bajo, ~0,032: coste por papel con tarifas Luna y tokens medidos (conversador 0,0020; guardián 0,0023; evaluador 0,0011; señales 0,0009 USD) multiplicado por la relación de coste por tarea Terra/Luna del índice (Luna 0,01 USD en `low` y 0,04 en `medium`: ×7 para el conversador y ×4,25 para el resto). Extremo alto, ~0,062: mismos tokens con las tarifas Terra de esta página |
| Conversación completa del 04/11/2026 | 0,0549 USD | Medido en trazas: 14 llamadas del conversador, 10 del guardián, 8 de señales y 1 evaluación, sin incidencias |
| 52.000 candidaturas al año | 1.500–3.000 USD | Estimación a partir del rango anterior; solo modelo |
| 13 reclutadores al año | 242.000–559.000 USD | Supuesto con fuentes públicas del 04/11/2026: salario de selección en España (Indeed, JOIN) con ~32 % de cotización empresarial y en México (OCC) con ~30 % de cargas; extremos «los 13 en México» y «los 13 en España» |

Las estimaciones excluyen voz, telefonía, SMS e infraestructura.

Fuentes (modelo y precios comprobados el 31/10/2026):

- [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [GPT-6.1 Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra)
- [Precios API](https://developers.openai.com/api/docs/pricing)
- [Migración GPT-6](https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra.md#migration-quickstart)
- [Integración LangChain](https://docs.langchain.com/oss/python/integrations/chat/openai)
