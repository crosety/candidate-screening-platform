# Preparación de un entorno de producción

La composición Docker del repositorio es para **desarrollo local**. No debe
publicarse tal cual: usa credenciales de base de datos de desarrollo, procesos con
recarga y servicios internos sin autenticación.

Este documento cubre el endurecimiento de la configuración. Topología, capacidad,
costes, autoescalado, observabilidad, CI/CD y la puerta de evals están en
[despliegue y escalado](despliegue-y-escalado.md).

## Ajustes que fallan cerrados

`DJANGO_PRODUCTION=true` activa el perfil reforzado de `web.settings`. Exige hosts
explícitos (`DJANGO_ALLOWED_HOSTS`), orígenes CSRF y URL del chat con HTTPS, claves
privadas sin valores de ejemplo, `DEBUG=false` y que no exista `SEED_PASSWORD`.
Postgres requiere credenciales no predeterminadas y `sslmode=verify-full`; Redis,
`rediss://` autenticado. Las cookies de sesión y CSRF son `Secure`; la sesión dura
como máximo ocho horas y caduca al cerrar el navegador. HSTS y la redirección HTTPS
están activos y no se confía por defecto en cabeceras de proxy que el cliente pueda
falsificar. Los tests comprueban que se rechazan configuraciones inseguras.

## Infraestructura que cada despliegue debe configurar y verificar

- TLS, dominios, aislamiento de red y proxy de confianza. El ingress opcional para
  candidatos (`web.candidate_ingress`) no expone la consola y no sustituye esta
  configuración.
- Un rol propietario exclusivo para migraciones y un rol de ejecución sin propiedad,
  superusuario, `BYPASSRLS`, `CREATE` ni capacidad de modificar políticas. En el
  compose local el rol es propietario: `FORCE RLS` no elimina ese privilegio.
- Secretos propios del entorno, credenciales de MCP por cliente y servicios internos
  inaccesibles desde Internet, con renovación y recuperación ensayadas.
- Responsable del tratamiento, proveedores, transferencias, conservación y borrado de
  copias de seguridad acordados antes de tratar datos reales.
- Canales de mensajería y voz validados con emisor, agente y destinatarios de prueba
  autorizados.
- CI remota, revisión de permisos y recuperación. La carga se trata en
  [despliegue y escalado](despliegue-y-escalado.md).
