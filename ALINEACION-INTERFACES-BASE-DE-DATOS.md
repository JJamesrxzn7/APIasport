# Alineación entre interfaces, API y base de datos

**Fecha de revisión:** 2 de noviembre de 2026
**Alcance:** interfaces publicadas de Apex Sports E-commerce y Administración, descripciones técnicas compartidas y scripts de creación/constraints de PostgreSQL. La vista o scripts adicionales de vistas SQL no fueron incluidos en esta revisión; se considera que existen, según lo indicado por el responsable del proyecto.

## Conclusión ejecutiva

El esquema cubre la mayoría de las entidades que necesita el panel administrativo: usuarios y roles, catálogo, proveedores, compras, recepciones, inventario, clientes, ventas y entregas. Sin embargo, todavía no está completamente alineado con los flujos funcionales del e-commerce. Los principales bloqueos antes de conectar ambos clientes a la API son:

1. Los identificadores de producto del catálogo JSON no caben en `PRODUCTOS.id_Producto CHAR(10)`; los IDs de categoría del HTML/JSON también necesitan un mapeo explícito a `CATEGORIAS.id_Categoria CHAR(10)`.
2. El registro solicita nombre completo, correo y contraseña; el esquema exige nombre y apellido en `USUARIOS`, mientras `CLIENTES` exige además identificación y ciudad.
3. El checkout recoge datos de entrega y opciones de envío, pero el esquema no conserva una dirección completa e inmutable asociada a la compra ni el costo de envío.
4. `FACTURAS` no distingue claramente pedido, pago e invoice/factura fiscal; su estado predeterminado es `PAG`, aunque los métodos de pago de la interfaz son demostrativos.
5. El panel requiere conectar las acciones de sus formularios a los nuevos endpoints administrativos; autenticación, permisos por módulo/acción y recursos de servidor ya están implementados en la API.

Recomendación: conservar una sola API FastAPI para ambos frontends, usar la base como fuente de verdad y resolver los puntos 1–4 antes de habilitar pagos reales. La migración 004 implementa los flujos administrativos de forma transaccional y elimina los efectos duplicados de inventario de los triggers heredados. No exponer credenciales de PostgreSQL/Supabase a ninguna interfaz.

## Qué ya coincide

| Capacidad de interfaz | Modelo disponible | Evaluación |
|---|---|---|
| Categorías, marcas, productos y fotografías | `CATEGORIAS`, `MARCAS`, `PRODUCTOS`, `PRODUCTO_FOTOGRAFIAS` | Cobertura básica. La API puede proyectar sus relaciones al contrato del catálogo. |
| Cuenta asociada a perfil de cliente | `USUARIOS`, `CLIENTES.id_Usuario` único y opcional | Compatible con una cuenta por cliente. El registro debe crear ambas entidades en una transacción cuando se complete el perfil de compra. |
| Cuenta, roles y permisos del panel | `USUARIOS`, `ROLES`, `PERMISOS`, `USUARIOSxROLES`, `ROLESxPERMISOS` | Migración 004 siembra permisos por módulo/acción y la API comprueba cada operación; la asignación de roles/permisos queda restringida a superadministradores. |
| Carrito para una cuenta autenticada | `CARRITO` tiene `id_Usuario` y `uk_carrito_usuario` | Compatible con la decisión de exigir cuenta; la API debe validar precios y disponibilidad al confirmar. |
| Proveedores, órdenes de compra y recepciones | `PROVEEDORES`, `COMPRAS`, `PROxOC`, `RECEPCIONES`, `PROxREC` | La API incorpora altas/edición, órdenes y recepciones parciales; stock aumenta una sola vez al confirmar la recepción. |
| Ventas, líneas y entregas | `FACTURAS`, `PROxFAC`, `ENTREGAS`, `PROxENT` | La API crea ventas/facturas en borrador, descuenta stock al aprobar y registra entrega sin un segundo descuento. No confirma pagos ni emite factura fiscal. |
| Indicadores del panel | Datos subyacentes de productos, facturas, clientes, compras y auditoría | Resumen con periodo, serie diaria, ventas pagadas preexistentes, stock bajo, ventas recientes y actividad de auditoría. |

## Cambios necesarios en las interfaces

### E-commerce

La interfaz puede conservar su diseño, categorías, búsqueda, fichas, carrito, validaciones y accesibilidad. El cambio principal es de persistencia y autoridad: actualmente varias operaciones son locales y el servidor debe pasar a ser quien determina datos comerciales.

| Prioridad | Cambio requerido |
|---|---|
| Bloqueante | **Conectar el catálogo** a `GET /api/v1/products` y al detalle por identificador. Dejar el JSON estático solo como fixture/fallback de desarrollo, no como fuente de verdad en producción. Alinear los identificadores según la decisión del apartado de base de datos. |
| Bloqueante | **Sustituir el registro e inicio de sesión local** por endpoints de autenticación. No enviar ni almacenar hashes de contraseña en el navegador; nunca conservar contraseñas o tokens de recuperación en `localStorage`. El formulario actual solo solicita nombre, correo y contraseña: permitir nombre y apellido separados o completar el perfil del cliente posteriormente. |
| Bloqueante | **Completar el perfil requerido antes de comprar.** `CLIENTES` requiere identificación y ciudad además del nombre. Pedir esos datos en el alta o, preferiblemente, en un paso de perfil/checkout y mostrar validaciones del servidor. No inventar esos campos a partir del correo o del nombre. |
| Bloqueante | **Sincronizar el carrito de la cuenta** con `CARRITO` y `DETALLE_CARRITO`. Al iniciar sesión, definir una sola vez si el carrito local se combina con el del servidor; en caso de duplicados, sumar cantidades sujetas a stock y límites. No confiar en el precio guardado por el cliente. |
| Bloqueante | **Convertir checkout en creación de pedido persistente.** Enviar producto/identificador y cantidad, además de los datos de entrega y método seleccionado. El backend debe volver a leer precios y stock, calcular subtotal/impuestos/envío/total y guardar pedido y líneas dentro de una transacción. No aceptar subtotales o totales del navegador como autoridad. |
| Bloqueante | **Cambiar la confirmación del pedido.** Mostrar confirmación solamente después de que el backend devuelva el pedido persistido. La página de confirmación debe consultar el pedido autorizado por la cuenta; `sessionStorage` puede mantener estado transitorio, pero no ser el comprobante del pedido. |
| Bloqueante para producción | **Dejar de presentar pagos de demostración como pagos reales.** La validación local de tarjeta no procesa ni confirma un cobro. Mantener opciones como simulación claramente etiquetada hasta integrar un proveedor. Nunca enviar PAN/CVV a la API propia ni guardar datos de tarjeta; guardar solo identificadores/estado del proveedor y referencias no sensibles. Transferencia y pago en local necesitan estados explícitos de pendiente/confirmado. |
| Recomendado | **Sincronizar favoritos** con la cuenta si se promete que acompañan al usuario entre dispositivos. El esquema actual no tiene favoritos: agregar tabla si se requiere sincronización; si no, conservarlos en el navegador y explicar que no son persistentes en la cuenta. |
| Recomendado | **Añadir estado de carga, error, reintento, sesión vencida y sin permisos** en solicitudes de catálogo, carrito y checkout. Una falla de API no debe mostrarse como carrito vacío ni confirmación exitosa. |

### Panel administrativo

El panel en modo API ya autentica, consulta datos reales y muestra tablas. La API incorpora contratos para operaciones administrativas y el siguiente trabajo de interfaz es conectar los formularios a esos endpoints; las acciones locales no deben mostrarse como guardadas.

| Prioridad | Cambio requerido |
|---|---|
| API implementada | **Autenticación y permisos.** Sesión administrativa por invitación; operaciones protegidas por grants módulo/acción. Aplicar `migrations/004_admin_workflows.sql` y aprovisionar al menos un superadministrador. |
| API implementada | **Lectura administrativa.** Resumen por fecha, recursos paginados con búsqueda/estado/fechas, consultas de detalle con líneas y lookups para formularios. |
| API implementada | **Escrituras transaccionales.** Productos, proveedores, clientes, compras/recepciones, ajustes, ventas/facturas, entregas, usuarios y permisos tienen contratos de escritura; cada cambio administrativo audita al actor. |
| API implementada | **Ciclo de inventario aprobado.** Compras no cambian stock; recepción confirmada suma cantidades aceptadas; venta aprobada descuenta una vez; entrega completada no descuenta de nuevo; ajuste aplica delta firmado al aprobar. |
| Pendiente de interfaz | **Conectar formularios al API.** Enviar los contratos documentados y mostrar éxito solo después de confirmación del servidor. La vista actual de API es principalmente de consulta y todavía no invoca la mayoría de las escrituras. |
| Pendiente de negocio | **Pagos y emisión fiscal.** La API no confirma pagos, no cambia facturas a `PAG` y no genera una factura fiscal. La interfaz debe mantener explícita esa limitación. |
| Recomendado | **Conservar UX accesible con los estados de API:** carga, error recuperable, sin resultados, sin permiso, sesión expirada y confirmación de cambios, incluyendo anuncios para lectores de pantalla y foco predecible. |
| Recomendado | **Conservar UX accesible con los estados de API:** carga, error recuperable, sin resultados, sin permiso, sesión expirada y confirmación de cambios, incluyendo anuncios para lectores de pantalla y foco predecible. |

### Origen de sesión y despliegue de las interfaces

Las páginas se publican en `github.io` y se proyecta desplegar la API en otro dominio. CORS permite que el navegador lea respuestas de un origen permitido, pero no constituye autenticación. Las cookies `HttpOnly` entre sitios diferentes pueden sufrir restricciones de cookies de terceros.

Para sesiones seguras, se recomienda publicar las interfaces y API bajo un dominio propio y el mismo sitio registrable (por ejemplo, `shop.example.com`, `admin.example.com`, `api.example.com`), con cookies `Secure`, `HttpOnly`, política `SameSite` adecuada y protección CSRF. Si se mantienen GitHub Pages y un host API independiente, validar explícitamente la estrategia de sesión en los navegadores objetivo; no guardar tokens de larga duración en `localStorage` como atajo.

## Cambios recomendados en la base de datos

### 1. Resolver el identificador de producto antes de cargar el catálogo — bloqueante

El catálogo documentado usa IDs descriptivos como `futbol-balon-pro-match`; `PRODUCTOS.id_Producto` es `CHAR(10)`. Esos IDs no caben. Elegir una de estas opciones antes de implementar las rutas:

- **Preferida a largo plazo:** migrar `id_Producto` y todas sus FK (`PRODUCTO_FOTOGRAFIAS`, `PROxOC`, `PROxREC`, `PROxAJU`, `DETALLE_CARRITO`, `PROxFAC`, `PROxENT`) a `VARCHAR` de longitud suficiente o UUID. Mantener un mapa de IDs antiguos durante la migración si ya hay registros.
- **Alternativa válida:** mantener la clave interna `CHAR(10)` y cambiar los frontends para usar siempre la clave devuelta por la API; el identificador slug del JSON sería un slug separado y único, almacenado como columna `UNIQUE` si se necesita conservar enlaces legibles.

Los IDs de categorías también requieren atención: `CATEGORIAS.id_Categoria` es `CHAR(10)`, mientras las interfaces usan IDs/etiquetas de navegación como `mma`, `ropa` o nombres de categoría. Definir un mapeo estable entre el identificador público, el ID relacional y la etiqueta visible; no suponer que el texto de la categoría sea su PK.

No usar truncamiento, convertir automáticamente un slug a los primeros 10 caracteres ni depender del nombre del producto como clave.

### 2. Completar el modelo de pedido, pago y envío — bloqueante para checkout

El esquema tiene carrito, factura y entrega, pero no una entidad inequívoca para el pedido web pendiente de pago. `FACTURAS.ESTADO_FAC` por defecto `PAG` no representa una orden recién creada ni los medios de pago demostrativos.

- Preferir entidades separadas `PEDIDOS`/detalle de pedido, `PAGOS` y `ENTREGAS`, con estados y transiciones definidos. Una factura fiscal se emite según la regla contable/fiscal acordada, no simplemente por el hecho de crear el carrito o seleccionar un pago.
- Registrar una **instantánea de dirección de envío** por pedido (destinatario, teléfono, dirección, ciudad y referencia); no depender únicamente de `CLIENTES.cli_Direccion`, que puede cambiar.
- Registrar modalidad y costo del envío en el pedido, con moneda/tipo de cambio si aplica. La interfaz hoy calcula tarifas de ejemplo localmente; el valor final lo determina el servidor.
- Añadir a pagos método, estado (`pendiente`, `confirmado`, `fallido`, `reembolsado` u otros acordados), proveedor/referencia externa no sensible y fechas. No guardar número de tarjeta, CVV ni credenciales del proveedor.
- Definir idempotencia para la confirmación del checkout, a fin de evitar órdenes/cobros duplicados ante reintentos de red.
- Acordar si `FACTURAS.id_Usuario` identifica al empleado que registra una venta. No utilizarlo para identificar al comprador: el comprador ya se relaciona mediante `CLIENTES.id_Usuario`. Para ventas online sin operador, permitir ausencia del empleado o modelar el actor del sistema de forma explícita.

Si el alcance académico decide usar `FACTURAS` como pedido y factura a la vez, documentar esa regla y añadir campos/estados de pedido, pago y envío necesarios; es menos claro y puede no ajustarse al ciclo fiscal real, que debe confirmarse con el responsable contable.

### 3. Fortalecer inventario para las funciones del panel — bloqueante para movimientos reales

`PRODUCTOS` guarda saldos iniciales, ingresos, egresos, ajustes y saldo final, pero no incluye un mínimo de stock; la interfaz administrativa necesita alertas “bajo mínimo”. Además, mantener varios contadores actualizables manualmente puede desincronizar el stock.

- Añadir `pro_StockMinimo` (o una política por producto/ubicación) para calcular alertas.
- Registrar un libro de movimientos de inventario inmutable con producto, cantidad con signo/tipo, motivo, fecha, actor y referencia a recepción/venta/ajuste. Aplicar movimiento y saldo/proyección en una misma transacción.
- Definir si `pro_Saldo_Final` es una proyección mantenida por el backend/SQL o se calcula desde movimientos; no permitir que el frontend lo edite directamente.
- Añadir checks de cantidades permitidas y prevenir stock negativo según la regla de negocio.

### 4. Endurecer restricciones y tipos de datos — recomendado antes de operar

- Agregar `CHECK` para precios, costos, descuentos, subtotales, cantidades e intentos fallidos no negativos; definir también si cero es válido en cada campo.
- Definir estados permitidos para cada entidad con `CHECK` o tablas de catálogo y validar transiciones en la API.
- Cambiar timestamps de negocio a `TIMESTAMPTZ` (por ejemplo, creación, pago, recepción, venta y entrega) y almacenar/mostrar explícitamente la zona horaria.
- Revisar columnas `CHAR(n)`: PostgreSQL las rellena con espacios y dificulta comparación/serialización. Preferir `VARCHAR`/`TEXT` para IDs y textos; exponer valores normalizados desde la API.
- Definir unicidad case-insensitive para correo/username si deben ignorar mayúsculas y minúsculas (índice único sobre valor normalizado). Acordar si username es requerido: el formulario actual solo pide email.
- Revisar longitudes de email/teléfono/nombre/dirección contra formularios y validaciones. Un email de 60 caracteres puede ser demasiado restrictivo; no asumir que teléfonos siempre tienen exactamente 10 caracteres.
- Proteger integridad de líneas: `UNIQUE(id_Carrito, id_Producto)` en `DETALLE_CARRITO`; checks de cantidad positiva; asegurar cantidades recibidas/entregadas no mayores que las solicitadas, salvo regla documentada.
- Comprobar relaciones entre detalles y sus documentos: una línea recibida debe pertenecer a un producto de la orden de compra referida, y una línea entregada a un producto incluido en la venta/pedido. Los FKs actuales validan por separado recibo/entrega y producto, pero no garantizan esa pertenencia compuesta.
- Definir generación de identificadores. Muchas PKs `CHAR(...)` son `NOT NULL` pero no tienen `DEFAULT` ni secuencia; la API necesitará generarlos de forma segura o migrarlos a IDs generados por PostgreSQL. Evitar generadores basados solo en conteo de filas.

### 5. Seguridad, roles y auditoría — requerido para producción

- Sembrar roles/permisos iniciales (cliente, administrador y roles operativos acordados). El endpoint público de registro asigna el rol cliente en servidor; nunca acepta un `id_Rol` arbitrario del cliente.
- Evitar guardar tokens de recuperación reutilizables en texto claro en `usr_TokenRecuperacion`. Usar token de un solo uso almacenado como hash, con expiración y fecha de consumo; agregar verificación de correo si se decide requerirla.
- `LOG_AUDITORIA` necesita PK efectiva: el script de constraints la deja explícitamente sin PK/FK. Añadir clave primaria, fecha con zona horaria, acción/entidad/registro y actor nullable con política de borrado definida. Preferir `JSONB` para cambios estructurados y excluir siempre hashes, contraseñas, tokens, datos de tarjeta y secretos.
- Hacer los registros de auditoría append-only para el rol normal de la aplicación y paginar/filtrar sus lecturas por permisos.
- Ejecutar el backend con un usuario de base de datos de privilegios mínimos; no usar ni exponer la clave `service_role` de Supabase a los frontends. Gestionar schema changes mediante migraciones versionadas.

### 6. Opcionales según alcance de cuenta

- Si favoritos deben sincronizarse entre dispositivos, crear una tabla `FAVORITOS` con FK de usuario/producto y clave única por pareja; de lo contrario, no hace falta cambiar el esquema.
- Si un cliente puede guardar más de una dirección, modelar direcciones por cliente y copiar una instantánea al pedido.
- Si se requiere recuperación de contraseña en la interfaz, el modelo de token seguro descrito arriba y los endpoints de solicitud/consumo son necesarios.

## Contrato de datos que deben compartir las interfaces

Antes de reemplazar datos locales, documentar en OpenAPI:

- identificador canónico del producto, categoría y estado;
- moneda y representación del precio (la interfaz actual usa centavos enteros; la base usa `NUMERIC(7,2)`);
- campos de cuenta, perfil del cliente y cuáles son obligatorios para registro frente a checkout;
- códigos de estado y etiquetas visibles para compras, pagos, ventas y entregas;
- paginación, búsqueda, filtros, orden, fecha/zona horaria y formato de errores;
- permisos por operación y formato de respuesta/error de sesión expirada;
- política para fusionar carrito local al iniciar sesión y para favoritos.

La API debe serializar precios de forma decimal exacta o en centavos enteros; nunca usar `float` para cálculos de dinero. Las interfaces no deben calcular el total final ni enviar estados privilegiados.

## Orden sugerido de trabajo

1. Acordar la estrategia de IDs de producto, datos obligatorios del perfil, ciclo de pedido/pago/factura y roles.
2. Preparar migraciones de esquema con respaldo y datos de prueba; aplicar checks, índices y permisos.
3. Implementar autenticación, perfil, catálogo de solo lectura y contratos OpenAPI.
4. Conectar catálogo y cuenta del e-commerce; después carrito autenticado.
5. Implementar pedidos, envío, pagos y movimientos de inventario en transacciones.
6. Implementar login/roles del panel y conectar módulos administrativos.
7. Probar permisos, datos inválidos, conflictos de stock, reintentos, fallos de red y sesión expirada en ambas interfaces.

## Fuentes revisadas

- [E-commerce publicado](https://jjamesrxzn7.github.io/E-commerce/)
- [Panel administrativo publicado](https://jjamesrxzn7.github.io/Administracion-ecommerce/)
- [Descripción técnica del e-commerce](../Scripts/DESCRIPCION_TECNICA_ECOMMERCE.md)
- [Descripción técnica de administración](../Scripts/DESCRIPCION_TECNICA_ADMINISTRACION.md)
- [Guía de desarrollo y despliegue de API](../Scripts/gu_a_de_desarrollo_y_despliegue_de_api_rest.md)
- [Script de creación de tablas](../Scripts/Script-Creacion-Tablas.sql)
- [Script de constraints](../Scripts/Script-Constrains-Tablas.sql)
