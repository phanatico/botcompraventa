import logging
import time
from typing import Any
from decimal import Decimal

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Route
from sqlalchemy import text

from markupsafe import Markup

from bot.misc import EnvKeys
from bot.database.methods.audit import log_audit
from bot.database.methods.transactions import admin_balance_change
from bot.database.methods.update import change_user_telegram_id

logger = logging.getLogger(__name__)


def _extract_original_user_id(request: Request, model: Any) -> int | None:
    for key in ("pk", "identity", "id"):
        raw = request.path_params.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue

    path_parts = [part for part in request.url.path.rstrip("/").split("/") if part]
    for part in reversed(path_parts):
        try:
            return int(part)
        except ValueError:
            continue

    try:
        return int(getattr(model, "telegram_id", 0) or 0)
    except (TypeError, ValueError):
        return None


def _extract_related_id(value: Any, *attribute_names: str) -> int | None:
    if value is None:
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, str):
        raw = value.strip()
        if raw.isdigit():
            return int(raw)
        return None

    for attr in attribute_names:
        attr_value = getattr(value, attr, None)
        if isinstance(attr_value, int):
            return attr_value
        if isinstance(attr_value, str) and attr_value.strip().isdigit():
            return int(attr_value.strip())

    return None


class LoginRateLimiter:
    """In-memory rate limiter for login attempts by IP."""

    def __init__(self, max_attempts: int = 5, lockout_seconds: int = 900):
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._attempts: dict[str, list[float]] = {}
        self._last_cleanup: float = time.time()

    def is_blocked(self, ip: str) -> bool:
        if ip not in self._attempts:
            return False
        now = time.time()
        self._attempts[ip] = [t for t in self._attempts[ip] if now - t < self.lockout_seconds]
        return len(self._attempts[ip]) >= self.max_attempts

    def record_failure(self, ip: str) -> None:
        now = time.time()
        if now - self._last_cleanup > 600:
            self._attempts = {
                k: [t for t in v if now - t < self.lockout_seconds]
                for k, v in self._attempts.items()
                if any(now - t < self.lockout_seconds for t in v)
            }
            self._last_cleanup = now
        if ip not in self._attempts:
            self._attempts[ip] = []
        self._attempts[ip].append(now)

    def reset(self, ip: str) -> None:
        self._attempts.pop(ip, None)


_login_limiter = LoginRateLimiter()
from bot.database.main import Database
from bot.database.models.main import (
    User, Role, Categories, Goods, ItemValues,
    BoughtGoods, Operations, Payments, ReferralEarnings,
    AuditLog, PromoCodes, CartItems, Reviews,
)
from bot.misc.metrics import get_metrics
from bot.misc.caching import get_cache_manager


# Authentication
class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        ip = request.client.host

        if _login_limiter.is_blocked(ip):
            await log_audit("web_login_blocked", level="WARNING", details=f"ip={ip}", ip_address=ip)
            return False

        form = await request.form()
        username = form.get("username")
        password = form.get("password")

        if username == EnvKeys.ADMIN_USERNAME and password == EnvKeys.ADMIN_PASSWORD:
            if (
                username == "admin" and password == "admin"
                and ip not in ("127.0.0.1", "::1", "localhost")
            ):
                await log_audit("web_login_blocked_default_creds", level="WARNING", details=f"ip={ip}", ip_address=ip)
                return False
            request.session.update({"authenticated": True})
            _login_limiter.reset(ip)
            await log_audit("web_login", user_id=None, details=f"user={username}", ip_address=ip)
            return True

        _login_limiter.record_failure(ip)
        await log_audit("web_login_failed", level="WARNING", details=f"user={username}", ip_address=ip)
        return False

    async def logout(self, request: Request) -> bool:
        await log_audit("web_logout", ip_address=request.client.host)
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return request.session.get("authenticated", False)


def _safe_model_repr(model: Any, max_len: int = 500) -> str:
    """Return a truncated repr that excludes sensitive fields."""
    _sensitive = {"balance", "password", "secret", "token", "value"}
    parts = []
    for col in getattr(model, "__table__", None).columns if hasattr(model, "__table__") else ():
        if col.name in _sensitive:
            continue
        val = getattr(model, col.name, None)
        parts.append(f"{col.name}={val!r}")
    result = f"{type(model).__name__}({', '.join(parts)})"
    return result[:max_len]


# Audited base view for mutable models
class AuditModelView(ModelView):
    async def after_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        action = f"sqladmin_{'create' if is_created else 'update'}"
        await log_audit(
            action,
            resource_type=self.name,
            resource_id=str(getattr(model, 'id', getattr(model, 'name', None))),
            details=_safe_model_repr(model),
            ip_address=request.client.host,
        )

    async def after_model_delete(self, model: Any, request: Request) -> None:
        await log_audit(
            "sqladmin_delete",
            resource_type=self.name,
            resource_id=str(getattr(model, 'id', getattr(model, 'name', None))),
            details=_safe_model_repr(model),
            ip_address=request.client.host,
        )


# Model Views
class UserAdmin(AuditModelView, model=User):
    column_list = [User.telegram_id, User.username, User.first_name, User.balance, User.role_id, User.referral_id,
                   User.registration_date, User.is_customer_active, User.is_blocked]
    form_columns = [User.telegram_id, User.username, User.first_name, User.balance, User.role_id,
                    User.referral_id, User.is_customer_active, User.is_blocked]
    form_include_pk = True
    can_create = False
    column_searchable_list = [User.telegram_id, User.username, User.first_name]
    column_sortable_list = [User.telegram_id, User.balance, User.registration_date]
    column_default_sort = (User.registration_date, True)
    name = "Usuario"
    name_plural = "Usuarios"
    icon = "fa-solid fa-users"

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        if is_created:
            return

        original_telegram_id = _extract_original_user_id(request, model)
        desired_telegram_id = data.get("telegram_id", getattr(model, "telegram_id", None))
        try:
            desired_telegram_id = int(desired_telegram_id) if desired_telegram_id is not None else None
        except (TypeError, ValueError):
            raise ValueError("Telegram ID no valido.")

        async with Database().session() as s:
            persisted = await s.get(User, original_telegram_id) if original_telegram_id else None
            if not persisted:
                raise ValueError("Usuario no encontrado.")

            if (
                original_telegram_id
                and desired_telegram_id
                and desired_telegram_id != original_telegram_id
            ):
                existing_target = await s.get(User, desired_telegram_id)
                if existing_target:
                    raise ValueError("Ya existe un usuario con ese Telegram ID.")
                request.state._pending_telegram_id_change = (original_telegram_id, desired_telegram_id)
                model.telegram_id = original_telegram_id

            if "balance" in data:
                old_balance = Decimal(str(persisted.balance or 0))
                new_balance = Decimal(str(data.get("balance") or model.balance or 0))
                delta = new_balance - old_balance
                request.state._pending_balance_delta = delta

                # Avoid double-applying the balance if SQLAdmin writes the edited value directly.
                model.balance = old_balance


    async def after_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        final_telegram_id = getattr(model, "telegram_id", None)
        pending_telegram_id_change = getattr(request.state, "_pending_telegram_id_change", None)
        if not is_created and pending_telegram_id_change:
            old_telegram_id, new_telegram_id = pending_telegram_id_change
            success, result = await change_user_telegram_id(old_telegram_id, new_telegram_id)
            if not success:
                raise ValueError(f"No se pudo cambiar el Telegram ID: {result}")
            final_telegram_id = new_telegram_id
            model.telegram_id = new_telegram_id

            try:
                from bot.main import auth_middleware
                if auth_middleware:
                    auth_middleware.invalidate_admin_cache(old_telegram_id)
                    auth_middleware.invalidate_admin_cache(new_telegram_id)
                    auth_middleware.blocked_users.discard(old_telegram_id)
            except Exception:
                pass

        await super().after_model_change(data, model, is_created, request)
        delta = getattr(request.state, "_pending_balance_delta", None)
        if not is_created and delta and delta != 0:
            await admin_balance_change(final_telegram_id or model.telegram_id, delta)


_PERM_FLAGS = [
    (1,   "USE"),
    (2,   "BROADCAST"),
    (4,   "SETTINGS"),
    (8,   "USERS"),
    (16,  "CATALOG"),
    (32,  "ADMINS"),
    (64,  "OWNER"),
    (128, "STATS"),
    (256, "BALANCE"),
    (512, "PROMOS"),
]


def _format_perms_html(model, name):
    perms = getattr(model, name, 0) or 0
    if not perms:
        return Markup('<span style="color:#999">\u2014</span>')
    badges = []
    for bit, label in _PERM_FLAGS:
        if perms & bit:
            badges.append(
                f'<span style="display:inline-block;background:#e2e8f0;padding:1px 6px;'
                f'border-radius:4px;margin:1px;font-size:12px">{label}</span>'
            )
    raw = f'<span style="color:#999;font-size:11px;margin-left:4px">({perms})</span>'
    return Markup(" ".join(badges) + raw)


class RoleAdmin(AuditModelView, model=Role):
    column_list = [Role.id, Role.name, Role.default, Role.permissions]
    form_columns = [Role.name, Role.default, Role.permissions]
    column_details_exclude_list = ["users"]
    column_sortable_list = [Role.id, Role.name]
    name = "Rol"
    name_plural = "Roles"
    icon = "fa-solid fa-shield-halved"
    column_formatters = {"permissions": _format_perms_html}
    column_formatters_detail = {"permissions": _format_perms_html}
    form_args = {
        "permissions": {
            "description": (
                "Bitmask value — sum the flags you need: "
                "USE=1, BROADCAST=2, SETTINGS=4, USERS=8, CATALOG=16, ADMINS=32, "
                "OWNER=64, STATS=128, BALANCE=256, PROMOS=512. "
                "Example: 927 = full Admin, 1023 = all (Owner)."
            ),
        },
    }


class CategoryAdmin(AuditModelView, model=Categories):
    column_list = [Categories.id, Categories.name]
    form_columns = [Categories.name]
    column_searchable_list = [Categories.name]
    column_sortable_list = [Categories.id, Categories.name]
    name = "Categoria"
    name_plural = "Categorias"
    icon = "fa-solid fa-folder"


class GoodsAdmin(AuditModelView, model=Goods):
    column_list = [Goods.id, Goods.name, Goods.price, Goods.duration_days, Goods.is_renewable, Goods.is_active, Goods.description, Goods.category_id]
    form_columns = [Goods.name, Goods.price, Goods.description, Goods.duration_days, Goods.is_renewable, Goods.is_active, Goods.category]
    column_searchable_list = [Goods.name]
    column_sortable_list = [Goods.id, Goods.name, Goods.price]
    name = "Producto"
    name_plural = "Productos"
    icon = "fa-solid fa-box"
    form_args = {
        "category": {
            "description": "Selecciona aqui la categoria del producto.",
        },
    }
    form_ajax_refs = {
        "category": {
            "fields": ("name",),
        },
    }

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        category_id = (
            _extract_related_id(data.get("category"), "id", "category_id")
            or _extract_related_id(getattr(model, "category", None), "id", "category_id")
            or _extract_related_id(data.get("category_id"), "id", "category_id")
            or _extract_related_id(getattr(model, "category_id", None), "id", "category_id")
        )
        if not category_id:
            raise ValueError("Debes seleccionar una categoria valida.")

        async with Database().session() as s:
            category_exists = await s.get(Categories, category_id)
            if not category_exists:
                raise ValueError("La categoria indicada no existe.")

        model.category_id = category_id


class ItemValuesAdmin(AuditModelView, model=ItemValues):
    column_list = [
        ItemValues.id, ItemValues.item_id, ItemValues.account_username, ItemValues.account_password,
        ItemValues.account_url, ItemValues.status, ItemValues.is_infinity, ItemValues.assigned_user_id,
    ]
    form_columns = [
        ItemValues.item,
        ItemValues.account_username,
        ItemValues.account_password,
        ItemValues.account_url,
        ItemValues.value,
        ItemValues.is_infinity,
        ItemValues.status,
        ItemValues.assigned_user_id,
    ]
    column_searchable_list = [ItemValues.value, ItemValues.account_username, ItemValues.account_url]
    column_sortable_list = [ItemValues.id, ItemValues.item_id]
    name = "Credencial"
    name_plural = "Credenciales"
    icon = "fa-solid fa-warehouse"

    form_args = {
        "item": {
            "description": "Selecciona aqui el producto al que pertenece esta credencial.",
        },
        "value": {
            "description": "Opcional. Puedes dejarlo vacio si vas a vender usuario, contrasena y URL por separado.",
        },
        "status": {
            "description": "Usa 'available' para stock disponible. Los productos vendidos pasan a 'assigned'.",
        },
        "account_username": {
            "description": "Usuario de la cuenta entregada al comprador.",
        },
        "account_password": {
            "description": "Contrasena de la cuenta entregada al comprador.",
        },
        "account_url": {
            "description": "URL de acceso o login del servicio.",
        },
    }
    form_ajax_refs = {
        "item": {
            "fields": ("name",),
        },
    }

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        item_id = (
            _extract_related_id(data.get("item"), "id", "item_id")
            or _extract_related_id(getattr(model, "item", None), "id", "item_id")
            or _extract_related_id(data.get("item_id"), "id", "item_id")
            or _extract_related_id(getattr(model, "item_id", None), "id", "item_id")
        )
        if not item_id:
            raise ValueError("Debes seleccionar un producto valido.")

        async with Database().session() as s:
            goods_exists = await s.get(Goods, item_id)
            if not goods_exists:
                raise ValueError("El producto indicado no existe.")

        model.item_id = item_id
        if not data.get("status"):
            model.status = "available"


class BoughtGoodsAdmin(ModelView, model=BoughtGoods):
    column_list = [
        BoughtGoods.id, BoughtGoods.item_name, BoughtGoods.stock_username, BoughtGoods.stock_url,
        BoughtGoods.price, BoughtGoods.buyer_id, BoughtGoods.bought_datetime,
        BoughtGoods.starts_at, BoughtGoods.expires_at, BoughtGoods.duration_days,
        BoughtGoods.status, BoughtGoods.is_renewable, BoughtGoods.unique_id
    ]
    column_searchable_list = [BoughtGoods.item_name, BoughtGoods.buyer_id, BoughtGoods.unique_id]
    column_sortable_list = [BoughtGoods.id, BoughtGoods.bought_datetime, BoughtGoods.price]
    column_default_sort = (BoughtGoods.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Compra"
    name_plural = "Compras"
    icon = "fa-solid fa-cart-shopping"


class OperationsAdmin(ModelView, model=Operations):
    column_list = [Operations.id, Operations.user_id, Operations.operation_value,
                   Operations.operation_time]
    column_searchable_list = [Operations.user_id]
    column_sortable_list = [Operations.id, Operations.operation_time, Operations.operation_value]
    column_default_sort = (Operations.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Movimiento"
    name_plural = "Movimientos"
    icon = "fa-solid fa-money-bill-transfer"


class PaymentsAdmin(ModelView, model=Payments):
    column_list = [Payments.id, Payments.provider, Payments.external_id, Payments.user_id,
                   Payments.amount, Payments.currency, Payments.status, Payments.created_at]
    column_searchable_list = [Payments.user_id, Payments.external_id, Payments.provider]
    column_sortable_list = [Payments.id, Payments.created_at, Payments.amount, Payments.status]
    column_default_sort = (Payments.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Pago"
    name_plural = "Pagos"
    icon = "fa-solid fa-credit-card"


class ReferralEarningsAdmin(ModelView, model=ReferralEarnings):
    column_list = [ReferralEarnings.id, ReferralEarnings.referrer_id,
                   ReferralEarnings.referral_id, ReferralEarnings.amount,
                   ReferralEarnings.original_amount, ReferralEarnings.created_at]
    column_searchable_list = [ReferralEarnings.referrer_id, ReferralEarnings.referral_id]
    column_sortable_list = [ReferralEarnings.id, ReferralEarnings.created_at, ReferralEarnings.amount]
    column_default_sort = (ReferralEarnings.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Comision"
    name_plural = "Comisiones"
    icon = "fa-solid fa-handshake"


class AuditLogAdmin(ModelView, model=AuditLog):
    column_list = [AuditLog.id, AuditLog.timestamp, AuditLog.level, AuditLog.user_id,
                   AuditLog.action, AuditLog.resource_type, AuditLog.resource_id,
                   AuditLog.details, AuditLog.ip_address]
    column_searchable_list = [AuditLog.action, AuditLog.resource_type, AuditLog.details]
    column_sortable_list = [AuditLog.id, AuditLog.timestamp, AuditLog.level, AuditLog.action]
    column_default_sort = (AuditLog.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Auditoria"
    name_plural = "Auditorias"
    icon = "fa-solid fa-clipboard-list"


class PromoCodeAdmin(AuditModelView, model=PromoCodes):
    column_list = [PromoCodes.id, PromoCodes.code, PromoCodes.discount_type,
                   PromoCodes.discount_value, PromoCodes.max_uses, PromoCodes.current_uses,
                   PromoCodes.is_active, PromoCodes.expires_at, PromoCodes.created_at]
    column_searchable_list = [PromoCodes.code]
    column_sortable_list = [PromoCodes.id, PromoCodes.code, PromoCodes.created_at]
    column_default_sort = (PromoCodes.id, True)
    name = "Codigo promocional"
    name_plural = "Codigos promocionales"
    icon = "fa-solid fa-tag"


class CartItemsAdmin(ModelView, model=CartItems):
    column_list = [CartItems.id, CartItems.user_id, CartItems.item_name, CartItems.added_at]
    column_searchable_list = [CartItems.user_id, CartItems.item_name]
    column_sortable_list = [CartItems.id, CartItems.added_at]
    column_default_sort = (CartItems.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Carrito"
    name_plural = "Carritos"
    icon = "fa-solid fa-cart-plus"



class ReviewsAdmin(AuditModelView, model=Reviews):
    column_list = [Reviews.id, Reviews.user_id, Reviews.item_name,
                   Reviews.rating, Reviews.text, Reviews.created_at]
    column_searchable_list = [Reviews.user_id, Reviews.item_name]
    column_sortable_list = [Reviews.id, Reviews.rating, Reviews.created_at]
    column_default_sort = (Reviews.id, True)
    name = "Resena"
    name_plural = "Resenas"
    icon = "fa-solid fa-star"


# Health & Metrics Endpoints
async def health_check(request: Request) -> JSONResponse:
    health_status = {
        "status": "healthy",
        "checks": {},
    }

    try:
        async with Database().session() as s:
            await s.execute(text("SELECT 1"))
        health_status["checks"]["database"] = "ok"
    except Exception as e:
        logger.error(f"Health check database error: {e}")
        health_status["checks"]["database"] = "error"
        health_status["status"] = "unhealthy"

    cache = get_cache_manager()
    if cache:
        health_status["checks"]["redis"] = "ok" if cache._healthy else "degraded"
    else:
        health_status["checks"]["redis"] = "not configured"

    metrics = get_metrics()
    if metrics:
        health_status["checks"]["metrics"] = "ok"
        health_status["uptime"] = metrics.get_metrics_summary()["uptime_seconds"]

    status_code = 200 if health_status["status"] == "healthy" else 503
    return JSONResponse(health_status, status_code=status_code)


async def prometheus_metrics(request: Request) -> PlainTextResponse:
    if not request.session.get("authenticated"):
        return PlainTextResponse("Unauthorized", status_code=401)
    metrics = get_metrics()
    if not metrics:
        return PlainTextResponse("# Metrics not initialized\n", status_code=503)
    return PlainTextResponse(metrics.export_to_prometheus(), media_type="text/plain")


async def metrics_json(request: Request) -> JSONResponse:
    if not request.session.get("authenticated"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    metrics = get_metrics()
    if not metrics:
        return JSONResponse({"error": "Metrics not initialized"}, status_code=503)
    return JSONResponse(metrics.get_metrics_summary(), status_code=200)


# App Factory
def create_admin_app() -> Starlette:

    from bot.web.export import export_routes

    routes = [
        Route("/health", health_check),
        Route("/metrics", metrics_json),
        Route("/metrics/prometheus", prometheus_metrics),
    ] + export_routes

    app = Starlette(routes=routes)
    app.add_middleware(SessionMiddleware, secret_key=EnvKeys.SECRET_KEY, max_age=1800)

    auth_backend = AdminAuth(secret_key=EnvKeys.SECRET_KEY)
    admin = Admin(
        app,
        engine=Database().engine,
        authentication_backend=auth_backend,
        title="Telegram Shop Admin",
    )

    admin.add_view(UserAdmin)
    admin.add_view(RoleAdmin)
    admin.add_view(CategoryAdmin)
    admin.add_view(GoodsAdmin)
    admin.add_view(ItemValuesAdmin)
    admin.add_view(BoughtGoodsAdmin)
    admin.add_view(OperationsAdmin)
    admin.add_view(PaymentsAdmin)
    admin.add_view(ReferralEarningsAdmin)
    admin.add_view(AuditLogAdmin)
    admin.add_view(PromoCodeAdmin)
    admin.add_view(CartItemsAdmin)
    if EnvKeys.REVIEWS_ENABLED == "1":
        admin.add_view(ReviewsAdmin)

    return app
