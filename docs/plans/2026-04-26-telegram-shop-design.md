# Telegram Shop Rework Design

Fecha: 2026-04-26

## Objetivo

Convertir esta base en una tienda de productos digitales por saldo interno, sin pasarela automática, con control fuerte desde panel web y bot en español.

## Requisitos aprobados

- Idioma principal en español.
- Sin flujo de recarga automática por pasarela.
- El usuario entra al bot y queda registrado por `telegram_id`.
- El usuario no puede comprar hasta que un admin lo active manualmente.
- El saldo se recarga manualmente desde administración.
- Debe haber varios admins autorizados por `Telegram ID`.
- Los productos son digitales y el stock debe ser único por unidad.
- Cada unidad debe guardar `username`, `password` y `url`.
- No se puede repetir la misma unidad a distintos clientes.
- El sistema debe descontar saldo y stock en la misma operación.
- Cada producto debe poder definir duración en días.
- Cada compra debe guardar inicio, fin, estado y si es renovable.
- Aviso automático al cliente 1 día antes del vencimiento.
- Si no renueva, la compra pasa a vencida/cancelada dentro del sistema.
- Debe existir historial completo de compras y movimientos, también cuando expire.
- El canal de Telegram existente se mantiene activo.
- La gestión principal debe poder hacerse desde el panel web.

## Modelo funcional

### Usuarios

- Registro automático por `telegram_id`.
- Campo de activación manual para permitir o bloquear compras.
- Saldo interno gestionado por admins.
- Campos visibles para nombre y username de Telegram cuando existan.

### Admins

- Se reutiliza el sistema de roles existente.
- La autorización real se hace por `Telegram ID`.
- El `username` es solo informativo.

### Productos

Cada producto debe poder configurar:

- nombre
- categoría
- descripción
- precio
- duración en días
- renovable sí/no
- activo sí/no

### Stock único

Cada unidad de stock representa una credencial individual:

- `username`
- `password`
- `url`
- estado (`available`, `assigned`, `expired`, `cancelled`)

Una unidad asignada no vuelve a venderse.

### Compras / accesos

Cada compra debe guardar:

- usuario
- producto
- credencial entregada
- fecha de compra
- fecha de inicio
- fecha de fin
- duración en días
- estado (`active`, `expiring`, `expired`, `cancelled`)
- renovable sí/no
- si ya fue avisada por vencimiento

## Reglas de negocio

### Compra

La compra debe ser atómica:

1. comprobar usuario autorizado
2. comprobar producto activo
3. comprobar saldo suficiente
4. bloquear y reservar una credencial disponible
5. descontar saldo
6. registrar compra con vencimiento
7. marcar stock como asignado

Si algo falla, no se descuenta saldo ni se entrega credencial.

### Recarga manual

- El bot muestra instrucciones para enviar justificante por Telegram.
- El admin valida por fuera y ajusta saldo manualmente.
- Debe quedar trazabilidad en historial de operaciones.

### Vencimiento

- Aviso automático 1 día antes.
- Si llega la fecha fin sin renovación, la compra cambia de estado.
- El historial nunca se borra.
- Solo se ofrece renovar si el producto es renovable.

## Cambios técnicos previstos

- Corregir fallos del CRUD de categorías y productos.
- Corregir la edición de productos que hoy mezcla `category_id` con `category_name`.
- Añadir campos de negocio a modelos, consultas, panel admin y handlers.
- Añadir un gestor periódico para avisos y expiración.
- Cambiar el locale por defecto a español y desactivar el flujo ruso.
- Mantener el envío al canal de Telegram.

## Riesgos conocidos

- La copia local no contiene `.git`.
- No hay `python`/`pytest` disponibles en PATH en esta sesión, así que la verificación inicial será estática salvo que aparezca el runtime más adelante.
- Para subir a GitHub hará falta un repositorio destino y credenciales o acceso configurado.
