# RAG sobre la FAQ de repartidores

La edición está en **Biblioteca**; el índice de una versión publicada se consulta
en su sección desplegable. En configuración explícita v3, cada oferta selecciona
sus fuentes y la búsqueda filtra esos IDs antes del top-3. Una selección vacía no
recupera documentos. Las fuentes desactivadas no se indexan ni se recuperan en v3;
los perfiles históricos mantienen su comportamiento anterior. Véase
[Configuración de ofertas](configuracion-ofertas.md).

## Fuente

- `seeds/tenants/grupo_sazon/sources/faq_repartidores.md`, versión 1.0 del 28/10/2026, con `id` previsto `faq_repartidores` y `mode: "search"`.
- Tiene 49 preguntas en español y 15 entradas en inglés para las preguntas más frecuentes.
- Es contenido de ejemplo, sin datos de una empresa real. El comentario interno de la primera línea lo indica y nunca se indexa. Las respuestas usan una redacción neutra.
- Es coherente con el manual, la FAQ de selección, el brief y el modelo operativo. No da cifras de sueldo ni pluses. Las condiciones de México que no constan se remiten a «lo confirma selección del país».
- Las entradas en inglés son **entradas separadas**, no bloques bilingües:
  - cada fragmento se mantiene monolingüe y su vector no se diluye;
  - el agente cita el texto en el idioma del candidato sin traducir hechos (regla 9 del prompt).
- Las preguntas que solo existen en español se recuperan también desde consultas en inglés, como mide el eval. El coste es que la versión española y la inglesa de una misma pregunta pueden ocupar dos de los tres huecos.

## Troceado (`screening/application/faq.py`)

- Si una fuente de búsqueda tiene líneas `## `, cada pregunta es un fragmento: `## pregunta` + respuesta. Una pregunta sin respuesta no se indexa.
- Solo se parte una sección que supera 1.000 caracteres. Se corta por párrafo, línea, frase o palabra, y cada trozo repite su `## pregunta`.
- `offset` apunta al inicio del encabezado en el texto original. Es único por fuente, como exige `unique_faq_fragment`.
- No se indexan:
  - los comentarios HTML `<!-- … -->`, que son metadatos de autoría;
  - las líneas `# ` de título o sección.

  Se enmascaran con espacios para conservar los offsets. El texto anterior a la primera pregunta se indexa sin pregunta si contiene algo más.
- Una fuente sin `## ` mantiene el comportamiento anterior: ventanas de 1.000 caracteres cada 800. La FAQ actual genera 64 fragmentos, por debajo del máximo de 100 por indexación.

## Cita

`search` añade a cada fragmento estos campos:

- `question`: el encabezado, o vacío en las ventanas antiguas;
- `citation`: `faq_repartidores › ¿Necesito moto propia?`, o solo el `source_id` si no hay pregunta.

Ambos se reconstruyen a partir del texto almacenado, así que no cambian el puerto `FaqStore` ni la tabla pgvector.

## Umbral y resultados medidos

Se mide con `tests/fixtures/faq_eval.json`, que tiene 33 consultas:

- 15 paráfrasis en español;
- 7 consultas en español de México;
- 4 en inglés sobre entradas que solo existen en español;
- 2 en inglés;
- 5 sin respuesta en la FAQ.

Los vectores reales de `text-embedding-3-small` están en `tests/fixtures/faq_embeddings.json`.

| Umbral de coseno | hit@3 | Sin respuesta rechazadas |
|---|---|---|
| 0,30–0,41 (se mantiene **0,35**) | **27/28 (96,4 %)** | **1/5** |
| 0,44 | 25/28 | 3/5 |
| 0,50 | 21/28 | 3/5 |

- Con 0,35 también se obtiene hit@1 de 27/28 y un MRR de 0,97. El único fallo es «do you pay per hour or per delivery?»: la respuesta sobre el sueldo queda en la posición 6, con un coseno de 0,32.
- 0,35 queda a mitad del hueco entre la consulta ajena al dominio (0,24) y el acierto más débil (0,41), por eso no se cambia.
- Las preguntas del dominio que la FAQ no responde puntúan entre 0,43 y 0,53, igual que muchos aciertos. Son el aparcamiento, el descuento en comida, el alquiler y el perro.
- Ningún umbral por coseno ni por puntuación z por consulta separa ambos grupos. Por ejemplo, «descuento de empleados» obtiene 0,50 (z = 2,78) y supera a un acierto con 0,49 (z = 2,48).
- Subir el umbral a 0,44 rechaza 3 de 5 consultas sin respuesta, pero pierde dos respuestas reales, y los márgenes a ambos lados son de milésimas. Sería sobreajustar.
- La defensa frente a estas consultas es la lectura del agente: con `citation` y la instrucción «sin evidencia, no inventes».

### Alternativa medida, no adoptada

Puntuar «coseno − coseno con el centroide de la FAQ» con un mínimo de 0,03 obtiene 27/28 y 3/5. Exige que el almacén exponga el centroide por versión (`avg(embedding)` en pgvector), lo que supone un cambio de puerto y de adaptador. El margen, de unos 0,03, sale de solo 5 negativos.

El test `tests/application/test_faq_eval.py` ejecuta `FaqService` con `MemoryFaqStore` y los vectores grabados, sin red. Exige al menos un 0,96 de hit@3 y un 0,2 de rechazo de consultas sin respuesta. Si el perfil ya contiene `faq_repartidores`, comprueba que genera los mismos fragmentos que el `.md`.

## Volver a grabar

Si cambia la FAQ, el troceado o las consultas, el test falla e indica qué texto no tiene vector. Para volver a grabar:

```sh
uv run python tools/record_faq_embeddings.py          # solo embeddings, solo textos nuevos
uv run python tools/record_faq_embeddings.py --check  # sin red: ¿está al día?
```

- La clave se lee del fichero indicado en `OPENAI_API_KEY_FILE`; por defecto, `.runtime-secrets/openai_api_key`. Nunca se imprime ni se escribe.
- La clave del fixture es el SHA-256 del texto exacto. Los vectores se redondean a 5 decimales y se eliminan los obsoletos.
- La grabación completa del 28/10/2026 usó 4.500 tokens, con un coste aproximado de 0,00009 USD.
- Después de grabar, revisa la tabla y ajusta los mínimos del test con las nuevas cifras.
