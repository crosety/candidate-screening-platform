# Grupo Sazón · datos de semilla

Cliente de referencia: cadena de restauración que contrata repartidores en moto en
España y México. Todos los datos son sintéticos.

- **Locales:** 45, 20 en España y 25 en México (cinco ciudades). Coordenadas
  aproximadas para probar el buscador; no representan establecimientos ni rutas
  reales. Los minutos se estiman con distancia geográfica, no con tráfico.
- **Vacantes y agendas:** sintéticas, servidas por los conectores locales.
- **Perfil:** oferta `repartidores` con carnet booleano y tipo por separado,
  ciudad y zona, disponibilidad, turno, experiencia (años y plataforma) y fecha de
  incorporación ISO. El cribado no aplica la antigüedad española del permiso a otros
  países; la validación legal del permiso y del vehículo corresponde al equipo.
- **FAQ de repartidores:** contenido de ejemplo en español e inglés, recuperado por
  búsqueda semántica.
- **Personas de evals:** `offers/repartidores/personas.json`.
- **Candidaturas de ejemplo:** `workspace-example.json`, cargadas por `make seed`.

No vuelvas a ejecutar `seed_local` sobre una base con trabajo que quieras conservar:
los perfiles activos e históricos no se migran con una resemilla. Cualquier cambio del
borrador pasa por evals, publicación y aprobaciones.
