# Configurar empresa, ofertas y documentación

El FDE prepara un único borrador versionado por cliente. La consola separa los
lugares de edición según qué información se comparte; no crea una variante del
producto para cada empresa. Selección consulta las ofertas publicadas y las
candidaturas, pero no edita estas instrucciones.

| Lugar | Qué se edita | Qué no debe incluir |
|---|---|---|
| **Empresa** | Identidad del asistente, políticas, instrucciones comunes, conexiones, permisos, canales, autonomía y campos reutilizables | Requisitos exclusivos de un puesto |
| **Ofertas → Puesto y condiciones** | Título, estado, responsabilidades, condiciones y requisitos de esa oportunidad | Una segunda descripción de la empresa |
| **Ofertas → Entrevista y evaluación** | Instrucciones, datos que debe recoger y criterios de encaje junto a cada dato | La rúbrica que mide la calidad del asistente |
| **Ofertas → Documentación y herramientas** | Fuentes seleccionadas y subconjunto de permisos | Copias del texto de los documentos o credenciales |
| **Ofertas → Probar y publicar** | Vista previa del contexto efectivo, acceso a simulaciones y revisión conjunta | Una publicación independiente de esa oferta |
| **Biblioteca** | Texto, disponibilidad y modo de uso de cada documento compartido | Criterios de selección de candidatos |
| **Simulación → Calidad del agente** | Rúbrica y umbral de las evaluaciones del asistente | Puntuación de encaje del candidato |

## Campos y criterios

Cada oferta declara si **hereda los campos comunes** o mantiene **campos propios**.
Crear una copia independiente copia sus valores en ese momento, no un vínculo:
los cambios posteriores de la empresa no la alteran. Una lista propia vacía sigue
vacía; no reactiva los campos comunes. Volver a heredar pide confirmación y retira
los criterios cuyos campos no existan en la lista común. Si una clave coincide pero
cambia el tipo, el criterio pasa a comprobar presencia; la confirmación lo explica.

El mismo editor permite definir nombre visible, pregunta, tipo, opciones,
obligatoriedad, validaciones y correspondencia con el ATS. Los identificadores y
usos en filtros están dentro de **Identificación e integración**. Junto a cada
campo se configuran sus criterios de encaje: condición, peso y si es imprescindible.
El mínimo válido del dato valida el formato; el mínimo de un criterio puntúa el
encaje. Por ejemplo, cero años puede ser una respuesta válida aunque no alcance los
dos años que valora la oferta.

La puntuación de encaje no es una probabilidad ni un descarte automático. Un dato
sin evidencia permanece pendiente. La rúbrica de calidad del agente mide algo
distinto: presentación, cumplimiento de límites, tono y cierre de la conversación.

## Fuentes y herramientas

Un documento se edita una vez en **Biblioteca**. Cada oferta selecciona sus IDs;
una selección vacía significa ninguna fuente. En configuración explícita (v3),
desactivar una fuente la retira tanto del contexto como de la búsqueda documental.
En modo **Contexto** se incorpora su texto a la entrevista. En **Búsqueda documental**
se indexa por versión y se recuperan los fragmentos pertinentes. El filtro de fuentes
de la oferta se aplica antes de elegir los tres resultados, no después.

Los permisos de una oferta solo pueden restringir los de la empresa. Las
instrucciones en lenguaje natural no conceden permisos. Las etiquetas organizativas
no son filtros de seguridad: para limitar por centro, puesto o región los resultados
de una integración hay que configurar el alcance del conector; no basta con una
etiqueta o una frase en el prompt.

## Vista previa y publicación

**Actualizar vista previa** calcula en el servidor la combinación de empresa y
oferta, incluidos los cambios locales todavía sin guardar. Muestra campos, fuentes
activas, capacidades e instrucciones efectivas del chat web. No ejecuta modelos,
no guarda y no publica. La voz añade su contexto de canal específico durante la
sesión; esta vista no pretende ser su prompt íntegro.

Guardar cualquier sección actualiza el borrador común con control de revisión. Si
otra pestaña lo ha cambiado, hay que revisar y recargar antes de guardar. Una
publicación incluye todas las modificaciones guardadas del cliente, no solo la
oferta abierta en pantalla; los cambios comunes aparecen marcados en sus tarjetas.
Continúa por **Cambios**, **Simulación → Evaluaciones** y **Validar y publicar**.
Se mantienen la puerta de evals y las aprobaciones exigidas por el riesgo.

La navegación avisa si hay cambios locales sin guardar. Durante el guardado la
edición queda deshabilitada para no presentar como guardado un cambio posterior.
En Empresa, el copiloto solo propone políticas compartidas; en v3 recibe también
las ofertas como contexto y no puede devolver los destinos heredados `who`/`fit`.

Las sesiones iniciadas conservan su versión publicada. No se reescriben perfiles
históricos, conversaciones ni resultados para adoptar la nueva organización.

## Preparar una configuración anterior

Los perfiles v1/v2 conservan su resolución histórica: campos propios no vacíos o
campos comunes, y todas las fuentes si no existe selección explícita. La consola
los identifica como configuración anterior. No se deduce automáticamente qué texto
pertenece a cada puesto: esa decisión requiere un plan revisado.

`prepare_offer_configuration` recibe un JSON con descripción e instrucciones comunes
y una entrada por cada oferta existente: `source_ids`, permisos opcionales y
`move_legacy_requirements`. Esta última opción traslada los requisitos e
instrucciones anteriores a la oferta elegida. Todos sus campos quedan como copia
independiente. No crea ofertas ni cambia sus estados.

```sh
# Sustituye REV por la revisión que muestra la consola.
docker compose exec api python manage.py prepare_offer_configuration /ruta/plan.json \
  --tenant grupo_sazon --actor fde@orbio.local --expected-rev REV

# Tras revisar la validación, guarda con una copia privada nueva del borrador anterior.
docker compose exec api python manage.py prepare_offer_configuration /ruta/plan.json \
  --tenant grupo_sazon --actor fde@orbio.local --expected-rev REV \
  --backup /ruta/privada/borrador-anterior.json --apply
```

El plan debe clasificar exactamente las ofertas existentes. El plan incluido en
`seeds/tenants/grupo_sazon/offer-configuration.json` corresponde al espacio con las
cuatro ofertas históricas; no es el plan de una instalación nueva que solo contiene
Repartidores. La validación rechaza esa diferencia en lugar de añadir o quitar datos.
La copia se crea con permisos privados y nunca se sobrescribe. El comando solo
prepara un borrador: la publicación sigue el recorrido normal.
