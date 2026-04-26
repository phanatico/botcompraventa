# Telegram Shop Implementation Plan

Fecha: 2026-04-26

## Fase 1. Base de datos y modelos

- Añadir activación manual de clientes.
- Añadir metadatos de Telegram al usuario.
- Añadir duración, renovable y activo a productos.
- Añadir estructura de credenciales únicas al stock.
- Añadir fechas, estados y datos de acceso a compras.
- Crear migración para entornos nuevos y existentes.

## Fase 2. CRUD fiable y panel admin

- Corregir escritura silenciosa de categorías.
- Corregir escritura silenciosa de productos.
- Corregir edición de producto/categoría.
- Exponer nuevos campos en SQLAdmin.
- Mejorar vistas de usuarios, productos, stock y compras.

## Fase 3. Compra y saldo

- Bloquear compra para usuarios no activados.
- Mantener saldo manual.
- Descontar saldo y stock de forma atómica.
- Guardar historial completo de compra.

## Fase 4. Vencimientos

- Añadir servicio periódico de expiración.
- Avisar 1 día antes.
- Marcar compras vencidas/canceladas dentro del sistema.

## Fase 5. UX e idioma

- Poner español como locale por defecto.
- Traducir botones y mensajes de flujos críticos.
- Mantener canal Telegram.
- Cambiar la recarga para instrucciones manuales por Telegram.

## Fase 6. Verificación y despliegue

- Revisar migraciones.
- Revisar consistencia de consultas y handlers.
- Dejar pasos de conexión con GitHub y despliegue en dedicado.
