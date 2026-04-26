# Telegram Shop: Cuentas, Bulk y Stock Real

Fecha: 2026-04-26

## Objetivo de esta fase

Dejar el sistema funcional para vender cuentas digitales con:

- lenguaje claro en panel y bot
- control real de stock
- importacion masiva
- seguimiento real de compras activas, vencidas y renovables
- datos de cliente suficientes para soporte

Esta fase no sustituye el diseno general anterior. Lo concreta y cierra las decisiones que estaban difusas en torno a `Productos`, `Credenciales` y `Compras`.

## Terminologia final

### Panel y bot

- `Categorias`: agrupaciones del catalogo
- `Productos`: fichas comerciales visibles en tienda
- `Cuentas`: stock real y unidades entregables
- `Compras`: accesos ya vendidos a clientes
- `Usuarios`: personas que usan el bot

### Equivalencia tecnica

- el modelo actual `ItemValues` pasa a mostrarse como `Cuentas`
- el modelo actual `BoughtGoods` sigue siendo `Compras`

No se unifica `Producto` con `Cuenta` a nivel base de datos, porque:

- un producto puede tener varias cuentas disponibles
- una compra debe enlazar una cuenta concreta entregada
- separar ambos niveles permite stock real, renovaciones e historico fiable

## Requisitos cerrados

### 1. Renombrado funcional

- En panel: `Credenciales` pasa a llamarse `Cuentas`
- En formularios:
  - `Item` pasa a llamarse `Producto`
  - `Account Username` pasa a `Usuario Cuenta`
  - `Account Password` pasa a `Clave Cuenta`
  - `Account Url` pasa a `URL Cuenta`
  - `Value` pasa a `Valor libre`
  - `Is Infinity` pasa a `Stock infinito`

### 2. Usuarios

Se anaden dos campos nuevos a `Usuarios`:

- `email`
- `whatsapp`

Reglas:

- ambos campos son opcionales
- deben poder editarse desde panel
- deben mostrarse en vistas de compra y soporte
- `whatsapp` se guarda como texto para no romper formatos internacionales

### 3. Cuentas unicas

Cada `Cuenta` debe poder guardar:

- producto
- usuario cuenta
- clave cuenta
- url cuenta
- valor libre opcional
- estado
- usuario asignado
- fecha de asignacion

Estados reales:

- `available`
- `assigned`
- `expired`
- `cancelled`

Regla:

- una cuenta `assigned` no vuelve a venderse
- una cuenta `expired` pertenece al historico, no al stock disponible

### 4. Stock real

Se mostraran contadores reales por producto.

Definiciones:

- `stock_disponible`: cuentas con `status = available`
- `stock_asignado`: cuentas con `status = assigned`
- `stock_vencido`: cuentas con `status = expired`
- `stock_cancelado`: cuentas con `status = cancelled`
- `stock_infinito`: indicador separado, no se suma como unidades finitas

Reglas:

- la tienda debe mostrar productos activos aunque el stock sea 0
- la ficha del producto debe mostrar cuantas cuentas quedan disponibles
- el boton de compra puede existir, pero si el stock es 0 debe avisar claramente
- si el producto tiene stock infinito, se indica como `Ilimitado`

### 5. Bulk

Se implementan dos modos de carga masiva para no mezclar casos:

#### Modo A: Bulk de cuentas para un producto existente

Uso:

- seleccionas un `Producto`
- pegas 10 lineas
- se crean 10 `Cuentas` dentro de ese producto

Formato recomendado por linea:

`usuario|clave|url`

Formato ampliado opcional:

`usuario|clave|url|valor_libre`

Resultado:

- 1 producto
- N cuentas

#### Modo B: Bulk de productos unicos

Uso:

- activas `Modo bulk: productos unicos`
- pegas 10 lineas
- el sistema crea 10 productos y 10 cuentas, una por linea

Formato recomendado por linea:

`nombre_producto|precio|descripcion|duracion_dias|categoria|usuario|clave|url`

Formato ampliado opcional:

`nombre_producto|precio|descripcion|duracion_dias|categoria|usuario|clave|url|renovable|activo|valor_libre`

Resultado:

- N productos
- N cuentas
- cada producto nace con 1 sola cuenta asociada

Reglas comunes del bulk:

- validar lineas vacias o incompletas
- detectar duplicados del mismo lote
- detectar duplicados ya existentes
- mostrar resumen final:
  - creados
  - duplicados
  - invalidos
  - omitidos

### 6. Compras

La seccion `Compras` debe convertirse en la vista de control principal del negocio.

Cada compra debe mostrar como minimo:

- ID
- producto
- usuario cuenta entregado
- clave cuenta entregada
- url cuenta entregada
- cliente Telegram
- email del cliente
- whatsapp del cliente
- fecha inicio
- fecha fin
- dias restantes
- estado
- renovable
- precio pagado
- ID unico de pedido

Filtros minimos:

- todas
- activas
- vencen pronto
- vencidas
- canceladas
- renovables

Busqueda minima:

- por producto
- por telegram id
- por username
- por email
- por whatsapp

### 7. Contadores reales en compras

Los contadores de compras deben salir de datos reales, no de texto calculado manualmente.

Definiciones:

- `activas`: `status = active` y `expires_at > now`
- `vencen_pronto`: activas con vencimiento dentro del umbral configurado
- `vencidas`: `status = expired` o `expires_at <= now`
- `canceladas`: `status = cancelled`
- `renovables`: compras con `is_renewable = true`

### 8. Bot

Cambios en tienda:

- mostrar categorias activas
- mostrar productos activos de la categoria
- mostrar stock disponible real o `Ilimitado`
- confirmar compra antes de descontar saldo

Texto de ficha sugerido:

- nombre
- descripcion
- precio
- stock disponible
- duracion
- renovable si/no

Cambios en perfil:

- mantener `Mis compras`
- mostrar dias restantes de cada acceso

Cambios en recibo de compra:

- nunca mostrar variables crudas
- incluir acceso real entregado
- incluir fecha de vencimiento si existe

## Cambios tecnicos

### Modelos

#### `User`

Anadir:

- `email = Column(String(255), nullable=True, index=True)`
- `whatsapp = Column(String(32), nullable=True, index=True)`

#### `Goods`

Anadir campos calculados o consultas auxiliares para:

- `stock_available_count`
- `stock_assigned_count`
- `stock_expired_count`
- `stock_cancelled_count`

No necesariamente como columnas fisicas. Preferible con consultas agregadas o helpers.

#### `ItemValues`

Mantener modelo base, pero renombrarlo en UI como `Cuentas`.

Evaluar anadir:

- `provider_name` opcional si luego quieres separar proveedor
- `notes` opcional para observaciones internas
- `purchase_media_url` o referencia media solo si finalmente quieres mandar foto al comprar

#### `BoughtGoods`

Asegurar que ya guarda:

- stock_username
- stock_password
- stock_url
- starts_at
- expires_at
- duration_days
- status
- is_renewable

Anadir si hace falta para consulta mas comoda:

- `buyer_username_snapshot`
- `buyer_email_snapshot`
- `buyer_whatsapp_snapshot`

Esto evita perder contexto historico si luego cambias el usuario.

### Migraciones

Se necesitara una migracion que:

- anada `email` y `whatsapp` a `users`
- opcionalmente anada snapshots a `bought_goods`
- revise datos existentes de `item_values.status`
- normalice valores `NULL` o vacios

### Panel admin

#### Usuarios

- mostrar y editar `email`
- mostrar y editar `whatsapp`

#### Productos

- mostrar contador real de stock
- filtro por con stock / sin stock

#### Cuentas

- renombrar menu y etiquetas
- selector de producto por lista clara
- soporte para alta individual
- soporte para bulk producto existente
- soporte para bulk productos unicos

#### Compras

- tabla mas rica
- filtros de estado
- dias restantes calculados

## Estrategia de implementacion

### Fase 1. Estabilidad de nombres y stock

- renombrar `Credenciales` a `Cuentas`
- corregir textos de panel
- mostrar stock disponible real por producto
- mostrar stock real en bot

### Fase 2. Datos de usuario

- anadir `email`
- anadir `whatsapp`
- exponerlos en panel y compras

### Fase 3. Bulk

- bulk de cuentas para producto existente
- bulk de productos unicos
- resumen de importacion

### Fase 4. Compras como centro de control

- filtros
- dias restantes
- vistas activas/vencidas
- snapshots utiles

### Fase 5. Limpieza final

- revisar textos del bot
- revisar contadores
- revisar soporte y renovacion

## Riesgos y cuidados

- no tocar la logica de transaccion de compra sin tests manuales guiados
- no reusar cuentas `assigned`
- no confiar en contadores cacheados para decisiones de compra
- distinguir siempre entre:
  - producto visible
  - cuenta disponible
  - compra historica

## Criterios de aceptacion

Se dara por buena esta fase cuando:

- el panel muestre `Cuentas` en vez de `Credenciales`
- puedas crear cuentas una a una o en bulk
- puedas crear productos unicos en bulk
- el bot muestre stock real disponible
- la tienda y `Mis compras` no usen contadores falsos
- los usuarios tengan `email` y `whatsapp`
- `Compras` sirva para ver quien tiene que producto y cuanto le queda
