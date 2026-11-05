# Alojamiento gestionado por Orbio en AWS

- **Estado:** propuesta de diseño, 05/11/2026. No hay entorno desplegado.

## Contexto

Cada cliente aporta su proceso, sus sistemas y sus datos de candidatos, pero la
plataforma tiene que mejorar a la vez para todos: una sola versión del producto, la
misma puerta de evals y la misma observabilidad.

## Decisión

La plataforma se aloja en AWS y la opera Orbio. Nunca se instala en las
instalaciones del cliente. Hay dos modalidades con la misma imagen y el mismo
pipeline de release:

- **Plataforma compartida por región (UE y EE. UU.)**, por defecto: cada cliente es un
  tenant aislado con RLS, perfiles, conectores, secretos y trazas propios.
- **Despliegue dedicado** para quien lo exija por contrato, volumen o residencia de
  datos: base de datos y contenedores propios, en la región que pida el cliente.

Detalle operativo en [despliegue y escalado](../despliegue-y-escalado.md).

## Alternativas descartadas

- **Instalación en el cliente:** cada cliente quedaría en una versión distinta, sin
  la puerta de evals ni la observabilidad comunes, y cada mejora exigiría una
  migración por cliente.
- **Solo despliegues dedicados:** multiplica el coste de operación para clientes que
  no lo necesitan.

## Consecuencias y pendientes

- Hay que vincular cada secreto a su cliente antes de alojar clientes reales en la
  plataforma compartida.
- **Pendiente:** dónde se alojan los datos de un cliente con candidatos en varias
  regiones, como Grupo Sazón en España y México: una región para todo el tenant o un
  despliegue por región. Se decide con Orbio y el cliente según sus obligaciones de
  tratamiento.
- Pendiente de concretar con Orbio: cuentas AWS, regiones exactas, proveedores de
  modelos admitidos en cada región y acuerdos de tratamiento de datos.
