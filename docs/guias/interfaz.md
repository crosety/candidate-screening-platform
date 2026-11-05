# Guía · Interfaz

**Propósito.** Que el candidato tenga una experiencia de producto terminado en
cualquier dispositivo y que el equipo de selección y el FDE trabajen con evidencias
claras. **Alcance:** `apps/chat` (candidato), `apps/console` (selección y
configuración), textos visibles de la API y mensajes de canales.

## Principios

1. **Hablar en términos de la tarea de la persona**: escribir, hablar aquí, recibir
   una llamada, revisar una candidatura. No en términos de proveedores, protocolos o
   presupuestos.
2. **Evidencia antes que opinión.** En la consola, cada valoración muestra de dónde
   sale: cita, herramienta, versión del perfil.
3. **Calma visual.** Blanco, negro y grises, jerarquía tipográfica clara y un
   acento solo para el estado que importa.
4. **Móvil primero para el candidato; escritorio primero para la consola.**

## Invariantes

- **UI-1 · El recorrido del candidato es el de un producto real.** Sin etiquetas ni
  avisos de prueba, ensayo o entorno interno, sin nombres de proveedores, cifras de
  presupuesto ni detalles técnicos. La clasificación interna (pruebas, simulaciones
  del FDE) vive en el servidor y en la consola.
- **UI-2 · Identificación de IA y consentimiento** visibles antes de empezar, con el
  detalle de privacidad accesible pero separado del recorrido principal (`SEG-7`).
- **UI-3 · Idiomas completos.** Todo texto del chat del candidato existe en español e
  inglés (`apps/chat/src/i18n.ts`); no se escriben textos visibles fuera del
  diccionario. La consola está en español.
- **UI-4 · Errores accionables.** Cada error dice qué puede hacer la persona; nunca
  muestra trazas, códigos internos ni nombres de proveedores.
- **UI-5 · Sin datos de un cliente en la interfaz.** Campos, etiquetas y ofertas se
  pintan desde el perfil y el esquema del resultado, no desde constantes (`ARQ-4`).
- **UI-6 · Tipos desde la API.** Los frontales usan el cliente generado
  (`packages/api-client`, `make api-client`); no se duplican tipos de la API a mano.
- **UI-7 · Cifras con su denominador.** Toda métrica dice qué cuenta y sobre qué total
  («10 de 17 cerradas»). Las observaciones de ánimo o idioma se rotulan como
  observaciones, no como criterio.
- **UI-8 · Accesible.** Controles con nombre accesible y etiquetas asociadas, foco
  visible, navegación por teclado, contraste suficiente y estados anunciados
  (`role="status"`, `role="alert"`).
- **UI-9 · Estados completos.** Cada vista contempla cargando, vacío, error y sin
  permiso, con un siguiente paso claro.

## Recomendaciones

- **UI-R1.** Reutiliza los componentes existentes antes de crear otros: en la
  consola, `apps/console/src/components/ui` (y tarjetas de métricas e insignias de
  estado); en el chat, los componentes de `apps/chat/src/components`. Los dos
  frontales no comparten componentes; no importes de uno en el otro.
- **UI-R2.** Fechas y horas en el formato del idioma y en la zona del lugar al que se
  refieren, indicándola cuando no es la de quien mira.
- **UI-R3.** Mensajes al candidato cortos, de una o dos frases, una pregunta cada vez.
- **UI-R4.** Comprueba el cambio en el navegador a 375 px y en escritorio antes de
  darlo por terminado.
- **UI-R5.** La navegación de la consola (`apps/console/src/layout/AppLayout.tsx`)
  tiene cuatro grupos: **Selección** (Bandeja, Ofertas, Candidaturas, Agenda,
  Analítica), **Configuración FDE** (Empresa, Biblioteca, Simulación,
  Mejora continua), **Publicación** (Cambios, Validar y publicar, Versiones) y
  **Diagnóstico** (Trazas técnicas, Recordatorios). Una pantalla nueva encaja en esa
  estructura o se razona.

## Señales de deuda

- Pantallas que reimplementan el mismo patrón con pequeñas diferencias.
- Textos visibles fuera del diccionario de idiomas.
- Lógica de negocio calculada en el frontal en lugar de en la API.
- Ficheros de componente que mezclan datos, estado y presentación en cientos de
  líneas.

## Qué revisa el evaluador

- Textos visibles nuevos: idioma, diccionario, tono y ausencia de detalles internos.
- Referencias a clientes, ofertas o campos concretos en el código de los frontales.
- Tipos de la API escritos a mano o cliente generado desactualizado.
- Accesibilidad básica de los controles nuevos y estados de carga, vacío y error.
- Métricas sin denominador o sin explicar qué cuentan.
- Evidencia de que el cambio se ha visto en el navegador cuando altera la interfaz.
