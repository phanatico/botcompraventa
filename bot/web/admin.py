import logging
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from typing import Any

_TEMPLATES_DIR = str(Path(__file__).parent / "templates")

from sqladmin import Admin, ModelView, BaseView, expose
from sqladmin.authentication import AuthenticationBackend
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Route
from sqlalchemy import text, select

from markupsafe import Markup

from bot.misc import EnvKeys, format_dt, format_date, days_left, days_left_str
from bot.database.methods.audit import log_audit
from bot.database.methods.update import change_user_telegram_id
from bot.database.methods.read import get_stock_dashboard_rows, invalidate_item_cache, invalidate_stats_cache

logger = logging.getLogger(__name__)
PROTECTED_OWNER_IDS = {int(EnvKeys.OWNER_ID), 8353553507}


def _html_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw or "").lower() in {"1", "true", "on", "yes", "si"}


def _compose_account_value(username: str | None, password: str | None, url: str | None, fallback: str | None = None) -> str:
    if fallback:
        return fallback
    return "\n".join([
        f"Usuario: {username or '-'}",
        f"Contrasena: {password or '-'}",
        f"URL: {url or '-'}",
    ])


def _parse_bulk_account_lines(raw: str) -> tuple[list[dict[str, str | None]], list[str]]:
    entries: list[dict[str, str | None]] = []
    invalid_lines: list[str] = []

    for line_number, raw_line in enumerate((raw or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) not in (3, 4) or not parts[0] or not parts[1] or not parts[2]:
            invalid_lines.append(f"L{line_number}: {line}")
            continue
        entries.append({
            "username": parts[0],
            "password": parts[1],
            "url": parts[2],
            "value": parts[3] if len(parts) == 4 and parts[3] else None,
        })
    return entries, invalid_lines


def _parse_bulk_unique_lines(raw: str, base_name: str) -> tuple[list[dict[str, str | None]], list[str]]:
    entries: list[dict[str, str | None]] = []
    invalid_lines: list[str] = []
    sequence = 1

    for line_number, raw_line in enumerate((raw or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|")]

        if len(parts) in (3, 4):
            if not base_name:
                invalid_lines.append(f"L{line_number}: falta nombre base para crear productos unicos")
                continue
            username, password, url = parts[:3]
            value = parts[3] if len(parts) == 4 and parts[3] else None
            product_name = f"{base_name} {sequence}"
            sequence += 1
        elif len(parts) in (4, 5):
            product_name, username, password, url = parts[:4]
            value = parts[4] if len(parts) == 5 and parts[4] else None
        else:
            invalid_lines.append(f"L{line_number}: {line}")
            continue

        if not product_name or not username or not password or not url:
            invalid_lines.append(f"L{line_number}: {line}")
            continue

        entries.append({
            "product_name": product_name,
            "username": username,
            "password": password,
            "url": url,
            "value": value,
        })

    return entries, invalid_lines


def _render_tools_page(title: str, body: str, message: str = "", embedded: bool = False) -> HTMLResponse:
    notice = f"<div style='padding:12px 14px;margin:0 0 16px;border-radius:8px;background:#eef6ff;border:1px solid #bfdbfe'>{escape(message)}</div>" if message else ""
    # Floating back-to-admin button — visible in every mode. In iframe (embedded),
    # target=_top makes it navigate the parent window so the user truly returns
    # to the old SQLAdmin panel instead of just reloading inside the iframe.
    back_target = ' target="_top"' if embedded else ""
    back_button = (
        f'<a href="/admin"{back_target} '
        'style="position:fixed;top:14px;right:14px;z-index:9999;'
        'background:linear-gradient(135deg,#1d4ed8,#0ea5e9);color:white;'
        'padding:10px 16px;border-radius:999px;text-decoration:none;'
        'font-weight:700;font-size:13px;box-shadow:0 6px 20px rgba(15,23,42,.25)">'
        '← Panel principal</a>'
    )
    nav_block = "" if embedded else """
    <div class="nav">
      <a href="/admin">Volver al panel</a>
      <a href="/tools" class="secondary">Herramientas</a>
      <a href="/tools/stock" class="secondary">📦 Stock</a>
      <a href="/tools/purchases" class="secondary">🛒 Mis Compras</a>
      <a href="/tools/products/new" class="secondary">Nuevo producto</a>
      <a href="/tools/cuentas/bulk-existing" class="secondary">Bulk cuentas</a>
      <a href="/tools/cuentas/bulk-unique" class="secondary">Bulk productos unicos</a>
    </div>"""
    body_padding = "0" if embedded else "24px"
    wrap_radius = "0" if embedded else "14px"
    wrap_shadow = "none" if embedded else "0 10px 35px rgba(15,23,42,.08)"
    wrap_max = "100%" if embedded else "1480px"
    html = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>{escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; background:#f3f6fb; margin:0; padding:{body_padding}; color:#162036; }}
    .wrap {{ max-width: {wrap_max}; margin:0 auto; background:white; border-radius:{wrap_radius}; padding:24px 28px; box-shadow:{wrap_shadow}; }}
    h1 {{ margin:0 0 16px; font-size:28px; }}
    h2 {{ margin-top:28px; font-size:22px; }}
    .nav a {{ display:inline-block; margin:0 12px 12px 0; padding:10px 14px; background:#1d4ed8; color:white; text-decoration:none; border-radius:8px; }}
    .nav a.secondary {{ background:#475569; }}
    form {{ margin-top:18px; }}
    label {{ display:block; margin:14px 0 6px; font-weight:600; }}
    input, textarea, select {{ width:100%; box-sizing:border-box; padding:10px 12px; border:1px solid #cbd5e1; border-radius:8px; }}
    textarea {{ min-height:220px; resize:vertical; }}
    .row {{ display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:16px; }}
    .row-3 {{ display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:16px; }}
    .checkbox {{ display:flex; align-items:center; gap:10px; margin-top:16px; }}
    .checkbox input {{ width:auto; }}
    button {{ margin-top:18px; background:#16a34a; color:white; border:none; border-radius:8px; padding:12px 16px; cursor:pointer; font-weight:700; }}
    table {{ width:100%; border-collapse:collapse; margin-top:18px; }}
    th, td {{ border-bottom:1px solid #e2e8f0; padding:10px 8px; text-align:left; vertical-align:top; }}
    th {{ background:#f8fafc; }}
    code {{ background:#eff6ff; padding:2px 6px; border-radius:6px; }}
    .hint {{ color:#475569; font-size:14px; margin-top:6px; }}
    .ok {{ color:#166534; font-weight:700; }}
    .muted {{ color:#64748b; }}
    .tabs {{ display:flex; flex-wrap:wrap; gap:8px; margin:8px 0 14px; }}
    .tabs a {{ padding:8px 16px; border-radius:999px; background:white; color:#0f172a; text-decoration:none; font-weight:700; font-size:13px; border:2px solid #cbd5e1; transition:all .15s; }}
    .tabs a:hover {{ transform:translateY(-1px); }}
    .tabs a .count {{ background:#cbd5e1; color:#0f172a; padding:1px 9px; border-radius:999px; margin-left:8px; font-size:12px; font-weight:800; }}
    .tabs a.t-all.active {{ background:#0ea5e9; border-color:#0ea5e9; color:white; }} .tabs a.t-all.active .count {{ background:rgba(255,255,255,.25); color:white; }}
    .tabs a.t-active.active {{ background:#16a34a; border-color:#16a34a; color:white; }} .tabs a.t-active.active .count {{ background:rgba(255,255,255,.25); color:white; }}
    .tabs a.t-expiring.active {{ background:#f59e0b; border-color:#f59e0b; color:white; }} .tabs a.t-expiring.active .count {{ background:rgba(255,255,255,.25); color:white; }}
    .tabs a.t-expired.active {{ background:#dc2626; border-color:#dc2626; color:white; }} .tabs a.t-expired.active .count {{ background:rgba(255,255,255,.25); color:white; }}
    .tabs a.t-cancelled.active {{ background:#6b7280; border-color:#6b7280; color:white; }} .tabs a.t-cancelled.active .count {{ background:rgba(255,255,255,.25); color:white; }}
    .tabs a.t-renewable.active {{ background:#7c3aed; border-color:#7c3aed; color:white; }} .tabs a.t-renewable.active .count {{ background:rgba(255,255,255,.25); color:white; }}
    .tabs a.t-all {{ border-color:#0ea5e9; color:#0ea5e9; }}
    .tabs a.t-active {{ border-color:#16a34a; color:#16a34a; }}
    .tabs a.t-expiring {{ border-color:#f59e0b; color:#b45309; }}
    .tabs a.t-expired {{ border-color:#dc2626; color:#dc2626; }}
    .tabs a.t-cancelled {{ border-color:#6b7280; color:#475569; }}
    .tabs a.t-renewable {{ border-color:#7c3aed; color:#7c3aed; }}
    .filters {{ display:flex; flex-wrap:wrap; gap:10px; align-items:flex-end; margin-bottom:10px; }}
    .filters input, .filters select {{ width:auto; min-width:200px; padding:8px 10px; }}
    .filters button {{ margin:0; padding:9px 14px; }}
    .badge {{ display:inline-block; padding:3px 12px; border-radius:999px; font-size:12px; font-weight:700; }}
    .badge-active {{ background:#dcfce7; color:#166534; }}
    .badge-expired {{ background:#fee2e2; color:#991b1b; }}
    .badge-cancelled {{ background:#e2e8f0; color:#475569; }}
    .badge-expiring {{ background:#fef3c7; color:#92400e; }}
    .day-circle {{ display:inline-flex; align-items:center; justify-content:center; width:42px; height:42px; border-radius:999px; font-weight:800; font-size:14px; color:white; box-shadow:0 2px 6px rgba(15,23,42,.15); }}
    .day-circle.ok {{ background:linear-gradient(135deg,#22c55e,#16a34a); }}
    .day-circle.warn {{ background:linear-gradient(135deg,#fbbf24,#f59e0b); }}
    .day-circle.bad {{ background:linear-gradient(135deg,#ef4444,#dc2626); }}
    .day-circle.dead {{ background:linear-gradient(135deg,#94a3b8,#64748b); }}
    .actions {{ display:flex; gap:6px; }}
    .actions a {{ display:inline-flex; align-items:center; justify-content:center; width:34px; height:34px; border-radius:8px; text-decoration:none; font-size:16px; transition:transform .1s; }}
    .actions a:hover {{ transform:translateY(-1px); }}
    .actions .a-renew {{ background:#dbeafe; color:#1d4ed8; }}
    .actions .a-support {{ background:#fee2e2; color:#dc2626; }}
    .actions .a-view {{ background:#e0e7ff; color:#4338ca; }}
    .actions .a-disabled {{ background:#f1f5f9; color:#cbd5e1; cursor:not-allowed; pointer-events:none; }}
    .header-bar {{ display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:8px; }}
    .header-bar .total-pill {{ background:linear-gradient(135deg,#0ea5e9,#1d4ed8); color:white; padding:8px 18px; border-radius:999px; font-weight:800; font-size:16px; box-shadow:0 4px 14px rgba(29,78,216,.25); }}
    table.compact th, table.compact td {{ padding:8px 6px; font-size:13px; vertical-align:middle; }}
    table.compact code {{ font-size:12px; }}
    table.compact tr:hover td {{ background:#f8fafc; }}
    .stock-low td {{ background:#fff7ed; }}
    .stock-empty td {{ background:#fef2f2; }}
  </style>
</head>
<body>
  {back_button}
  <div class="wrap">
    {nav_block}
    <h1>{escape(title)}</h1>
    {notice}
    {body}
  </div>
</body>
</html>"""
    return HTMLResponse(html)


def _days_remaining(value: datetime | None) -> str:
    if not value:
        return "—"
    now = datetime.now(timezone.utc)
    delta = value - now
    return str(max(delta.days, 0)) if delta.total_seconds() > 0 else "0"


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
    column_list = [User.telegram_id, User.username, User.first_name, User.email, User.whatsapp, User.balance, User.role_id, User.referral_id,
                   User.registration_date, User.is_customer_active, User.is_blocked]
    form_columns = [User.telegram_id, User.username, User.first_name, User.email, User.whatsapp, User.balance, User.role_id,
                    User.referral_id, User.is_customer_active, User.is_blocked]
    form_include_pk = True
    column_searchable_list = [User.telegram_id, User.username, User.first_name, User.email, User.whatsapp]
    column_sortable_list = [User.telegram_id, User.balance, User.registration_date]
    column_default_sort = (User.registration_date, True)
    name = "Usuario"
    name_plural = "Usuarios"
    icon = "fa-solid fa-users"
    column_labels = {
        "telegram_id": "Telegram ID",
        "username": "Username",
        "first_name": "Nombre",
        "email": "Email",
        "whatsapp": "WhatsApp",
        "balance": "Saldo",
        "role_id": "Rol",
        "referral_id": "Referido por",
        "registration_date": "Alta",
        "is_customer_active": "Compra activa",
        "is_blocked": "Bloqueado",
    }

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        desired_telegram_id = data.get("telegram_id", getattr(model, "telegram_id", None))
        try:
            desired_telegram_id = int(desired_telegram_id) if desired_telegram_id is not None else None
        except (TypeError, ValueError):
            raise ValueError("Telegram ID no valido.")

        if desired_telegram_id in PROTECTED_OWNER_IDS:
            async with Database().session() as s:
                owner_role_id = (await s.execute(
                    select(Role.id).order_by(Role.permissions.desc()).limit(1)
                )).scalar()
            if owner_role_id:
                model.role_id = owner_role_id
                data["role_id"] = owner_role_id
            model.is_blocked = False
            data["is_blocked"] = False
            model.is_customer_active = True
            data["is_customer_active"] = True

        if is_created:
            if not desired_telegram_id:
                raise ValueError("Debes indicar un Telegram ID valido.")

            async with Database().session() as s:
                existing_target = await s.get(User, desired_telegram_id)
                if existing_target:
                    raise ValueError("Ya existe un usuario con ese Telegram ID.")
            model.telegram_id = desired_telegram_id
            return

        original_telegram_id = _extract_original_user_id(request, model)

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
    column_labels = {
        "id": "ID",
        "name": "Nombre",
        "price": "Precio",
        "duration_days": "Dias",
        "is_renewable": "Renovable",
        "is_active": "Activo",
        "description": "Descripcion",
        "category_id": "Categoria ID",
        "category": "Categoria",
    }
    form_args = {
        "category": {
            "description": "Selecciona la categoria en la lista o usa Herramientas > Nuevo producto rapido si prefieres un selector clasico.",
        },
    }


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
    name = "Cuenta"
    name_plural = "Cuentas"
    icon = "fa-solid fa-warehouse"
    column_labels = {
        "id": "ID",
        "item": "Producto",
        "item_id": "Producto ID",
        "account_username": "Usuario cuenta",
        "account_password": "Clave cuenta",
        "account_url": "URL cuenta",
        "value": "Valor libre",
        "is_infinity": "Stock infinito",
        "status": "Estado",
        "assigned_user_id": "Asignada a",
    }

    form_args = {
        "item": {
            "description": "Selecciona el producto de la lista o usa Herramientas > Bulk cuentas si vas a cargar muchas a la vez.",
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

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        if not data.get("status"):
            model.status = "available"
        if not data.get("value"):
            model.value = _compose_account_value(
                data.get("account_username"),
                data.get("account_password"),
                data.get("account_url"),
            )


class BoughtGoodsAdmin(ModelView, model=BoughtGoods):
    column_list = [
        BoughtGoods.id, BoughtGoods.item_name, BoughtGoods.stock_username, BoughtGoods.stock_url,
        BoughtGoods.price, BoughtGoods.buyer_id, BoughtGoods.buyer_email_snapshot, BoughtGoods.buyer_whatsapp_snapshot,
        BoughtGoods.bought_datetime,
        BoughtGoods.starts_at, BoughtGoods.expires_at, BoughtGoods.duration_days,
        BoughtGoods.status, BoughtGoods.is_renewable, BoughtGoods.unique_id
    ]
    column_searchable_list = [
        BoughtGoods.item_name, BoughtGoods.buyer_id, BoughtGoods.unique_id,
        BoughtGoods.buyer_username_snapshot, BoughtGoods.buyer_first_name_snapshot,
        BoughtGoods.buyer_email_snapshot, BoughtGoods.buyer_whatsapp_snapshot,
    ]
    column_sortable_list = [BoughtGoods.id, BoughtGoods.bought_datetime, BoughtGoods.price]
    column_default_sort = (BoughtGoods.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Compra"
    name_plural = "Compras"
    icon = "fa-solid fa-cart-shopping"
    column_labels = {
        "id": "ID",
        "item_name": "Producto",
        "stock_username": "Usuario cuenta",
        "stock_password": "Clave cuenta",
        "stock_url": "URL cuenta",
        "price": "Precio",
        "buyer_id": "Cliente Telegram",
        "buyer_email_snapshot": "Email cliente",
        "buyer_whatsapp_snapshot": "WhatsApp cliente",
        "bought_datetime": "Comprada",
        "starts_at": "Inicio",
        "expires_at": "Fin",
        "duration_days": "Duracion",
        "status": "Estado",
        "is_renewable": "Renovable",
        "unique_id": "Pedido",
    }
    column_formatters = {
        "expires_at": lambda model, name: f"{getattr(model, name) or '—'} ({_days_remaining(getattr(model, name))} dias)",
    }


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


def _ensure_tools_auth(request: Request) -> RedirectResponse | None:
    if not request.session.get("authenticated"):
        return RedirectResponse("/admin/login", status_code=302)
    return None


async def tools_home(request: Request) -> HTMLResponse:
    auth_redirect = _ensure_tools_auth(request)
    if auth_redirect:
        return auth_redirect

    embed = bool(request.query_params.get("embed"))
    qs = "?embed=1" if embed else ""

    body = f"""
    <p>Centro de control rapido del catalogo y las ventas.</p>
    <div class="row" style="margin-top:18px">
      <a href="/tools/stock{qs}" style="display:block;padding:18px;border-radius:12px;background:#1d4ed8;color:white;text-decoration:none;font-weight:700">
        📦 Stock por producto<br><span style="font-weight:400;font-size:13px">Disponible, asignado, vencido y cancelado en tiempo real.</span>
      </a>
      <a href="/tools/purchases{qs}" style="display:block;padding:18px;border-radius:12px;background:#0ea5e9;color:white;text-decoration:none;font-weight:700">
        🛒 Mis Compras<br><span style="font-weight:400;font-size:13px">Todas las compras con dias restantes, cliente y filtros por estado.</span>
      </a>
    </div>
    <div class="row" style="margin-top:14px">
      <a href="/tools/cuentas/bulk-unique{qs}" style="display:block;padding:18px;border-radius:12px;background:#16a34a;color:white;text-decoration:none;font-weight:700">
        ⚡ Bulk productos unicos<br><span style="font-weight:400;font-size:13px">Crea 10, 50 o 100 productos unicos con sus cuentas en un solo envio.</span>
      </a>
      <a href="/tools/cuentas/bulk-existing{qs}" style="display:block;padding:18px;border-radius:12px;background:#475569;color:white;text-decoration:none;font-weight:700">
        🧾 Bulk cuentas<br><span style="font-weight:400;font-size:13px">Anade muchas cuentas a un producto que ya existe.</span>
      </a>
    </div>
    <ul style="margin-top:24px;color:#475569">
      <li><b>Stock</b>: vista real de disponible, asignado, vencido y cancelado por producto.</li>
      <li><b>Mis Compras</b>: dashboard de pedidos con tabs (Activos, Por vencer, Vencidos, Cancelados) y dias restantes.</li>
      <li><b>Bulk productos unicos</b>: pega un bloque tipo <code>usuario|clave|url</code> y crea muchos productos unicos a la vez.</li>
      <li><b>Bulk cuentas</b>: pega muchas cuentas para un producto existente.</li>
    </ul>
    """
    return _render_tools_page("Herramientas de catalogo", body, embedded=bool(request.query_params.get("embed")))


async def stock_dashboard(request: Request) -> HTMLResponse:
    auth_redirect = _ensure_tools_auth(request)
    if auth_redirect:
        return auth_redirect

    q = (request.query_params.get("q") or "").strip().lower()
    category_filter = (request.query_params.get("category") or "").strip()
    status_filter = (request.query_params.get("status") or "").strip()  # all|active|inactive|low|empty
    embed = bool(request.query_params.get("embed"))
    embed_param = "&embed=1" if embed else ""
    embed_form = '<input type="hidden" name="embed" value="1">' if embed else ""

    rows = await get_stock_dashboard_rows()
    categories = sorted({str(r["category_name"]) for r in rows})

    def keep(row: dict) -> bool:
        if q and q not in row["name"].lower() and q not in str(row["category_name"]).lower():
            return False
        if category_filter and str(row["category_name"]) != category_filter:
            return False
        if status_filter == "active" and not row["is_active"]:
            return False
        if status_filter == "inactive" and row["is_active"]:
            return False
        if status_filter == "low":
            if row["has_infinite"]:
                return False
            if not (0 < int(row["available"]) <= 3):
                return False
        if status_filter == "empty":
            if row["has_infinite"]:
                return False
            if int(row["available"]) > 0:
                return False
        return True

    visible = [r for r in rows if keep(r)]

    total = len(rows)
    total_active = sum(1 for r in rows if r["is_active"])
    total_low = sum(1 for r in rows if not r["has_infinite"] and 0 < int(r["available"]) <= 3)
    total_empty = sum(1 for r in rows if not r["has_infinite"] and int(r["available"]) == 0)

    def tab(label: str, key: str, count: int) -> str:
        active_cls = "active" if status_filter == key or (key == "all" and not status_filter) else ""
        href_status = "" if key == "all" else f"&status={key}"
        href = f"/tools/stock?q={escape(q)}&category={escape(category_filter)}{href_status}{embed_param}"
        return f'<a class="{active_cls}" href="{href}">{escape(label)} <span class="count">{count}</span></a>'

    tabs_html = (
        '<div class="tabs">'
        f'{tab("Todos", "all", total)}'
        f'{tab("Activos", "active", total_active)}'
        f'{tab("Stock bajo", "low", total_low)}'
        f'{tab("Sin stock", "empty", total_empty)}'
        f'{tab("Inactivos", "inactive", total - total_active)}'
        '</div>'
    )

    cat_options = "".join(
        f'<option value="{escape(c)}"{" selected" if c == category_filter else ""}>{escape(c)}</option>'
        for c in categories
    )

    def render_row(row: dict) -> str:
        if row["has_infinite"]:
            disp = '<span class="badge badge-active">Ilimitado</span>'
            extra_cls = ""
        else:
            avail = int(row["available"])
            if avail == 0:
                disp = f'<span class="badge badge-expired">Sin stock</span>'
                extra_cls = "stock-empty"
            elif avail <= 3:
                disp = f'<span class="badge badge-expiring">{avail}</span>'
                extra_cls = "stock-low"
            else:
                disp = f'<span class="badge badge-active">{avail}</span>'
                extra_cls = ""
        active_badge = (
            '<span class="badge badge-active">Si</span>' if row["is_active"]
            else '<span class="badge badge-cancelled">No</span>'
        )
        return (
            f'<tr class="{extra_cls}">'
            f'<td>{row["id"]}</td>'
            f'<td>{escape(str(row["category_name"]))}</td>'
            f'<td><b>{escape(row["name"])}</b></td>'
            f'<td>{row["price"]}</td>'
            f'<td>{active_badge}</td>'
            f'<td>{disp}</td>'
            f'<td>{row["assigned"]}</td>'
            f'<td>{row["expired"]}</td>'
            f'<td>{row["cancelled"]}</td>'
            f'</tr>'
        )

    table_rows = "".join(render_row(r) for r in visible)

    body = f"""
    <p>Vista real de stock por producto. <b>Disponible</b> cuenta lo vendible ahora mismo. Si el producto es ilimitado se muestra como tal.</p>
    {tabs_html}
    <form method="get" class="filters">
      {embed_form}
      <input type="text" name="q" placeholder="Buscar producto o categoria..." value="{escape(q)}">
      <select name="category">
        <option value="">Todas las categorias</option>
        {cat_options}
      </select>
      <input type="hidden" name="status" value="{escape(status_filter)}">
      <button type="submit">Filtrar</button>
      <a href="/tools/stock?embed={"1" if embed else ""}" style="margin-left:6px;color:#475569;text-decoration:none">Limpiar</a>
    </form>
    <table class="compact">
      <thead>
        <tr>
          <th>ID</th>
          <th>Categoria</th>
          <th>Producto</th>
          <th>Precio</th>
          <th>Activo</th>
          <th>Disponible</th>
          <th>Asignado</th>
          <th>Vencido</th>
          <th>Cancelado</th>
        </tr>
      </thead>
      <tbody>{table_rows or "<tr><td colspan='9' class='muted'>No hay productos que coincidan con el filtro.</td></tr>"}</tbody>
    </table>
    <p class="hint">Mostrando {len(visible)} de {total} productos. Stock bajo = 1-3 disponibles. Sin stock = 0 disponibles.</p>
    """
    return _render_tools_page("📦 Dashboard de stock", body, embedded=bool(request.query_params.get("embed")))


async def quick_new_product(request: Request) -> HTMLResponse:
    auth_redirect = _ensure_tools_auth(request)
    if auth_redirect:
        return auth_redirect

    message = ""
    created_name: str | None = None
    async with Database().session() as s:
        categories = (await s.execute(
            select(Categories.id, Categories.name).order_by(Categories.name.asc())
        )).all()

    if request.method == "POST":
        form = await request.form()
        name = (form.get("name") or "").strip()
        description = (form.get("description") or "").strip()
        category_id_raw = (form.get("category_id") or "").strip()
        price_raw = (form.get("price") or "").strip()
        duration_days_raw = (form.get("duration_days") or "30").strip()
        is_renewable = _html_bool(form.get("is_renewable"))
        is_active = _html_bool(form.get("is_active"))

        try:
            category_id = int(category_id_raw)
            duration_days = max(int(duration_days_raw), 1)
            price = Decimal(price_raw)
        except (TypeError, ValueError, InvalidOperation):
            message = "Revisa categoria, precio y duracion."
        else:
            async with Database().session() as s:
                exists_goods = (await s.execute(
                    select(Goods.id).where(Goods.name == name)
                )).scalar()
                exists_category = (await s.execute(
                    select(Categories.id).where(Categories.id == category_id)
                )).scalar()
                if not name or not description or exists_goods:
                    message = "El nombre es obligatorio y no puede estar duplicado."
                elif not exists_category:
                    message = "La categoria seleccionada no existe."
                else:
                    s.add(Goods(
                        name=name,
                        description=description,
                        price=price,
                        category_id=category_id,
                        duration_days=duration_days,
                        is_renewable=is_renewable,
                        is_active=is_active,
                    ))
                    created_name = name
                    message = f"Producto creado: {name}"
            if created_name:
                await invalidate_item_cache(created_name)
                await invalidate_stats_cache()

    options = "".join(
        f"<option value='{category_id}'>{escape(category_name)}</option>"
        for category_id, category_name in categories
    )
    body = f"""
    <p>Crea productos usando las categorias existentes con selector clasico por raton.</p>
    <form method="post">
      <label>Nombre</label>
      <input type="text" name="name" required>
      <label>Descripcion</label>
      <textarea name="description" required></textarea>
      <div class="row-3">
        <div>
          <label>Precio</label>
          <input type="text" name="price" value="5.00" required>
        </div>
        <div>
          <label>Duracion dias</label>
          <input type="number" name="duration_days" value="30" min="1" required>
        </div>
        <div>
          <label>Categoria</label>
          <select name="category_id" required>{options}</select>
        </div>
      </div>
      <label class="checkbox"><input type="checkbox" name="is_renewable" checked> Renovable</label>
      <label class="checkbox"><input type="checkbox" name="is_active" checked> Activo</label>
      <button type="submit">Crear producto</button>
    </form>
    """
    return _render_tools_page("Nuevo producto rapido", body, message=message, embedded=bool(request.query_params.get("embed")))


async def bulk_accounts_existing(request: Request) -> HTMLResponse:
    auth_redirect = _ensure_tools_auth(request)
    if auth_redirect:
        return auth_redirect

    message = ""
    async with Database().session() as s:
        products = (await s.execute(
            select(Goods.id, Goods.name).order_by(Goods.name.asc())
        )).all()

    if request.method == "POST":
        form = await request.form()
        item_id_raw = (form.get("item_id") or "").strip()
        raw_lines = form.get("bulk_lines") or ""
        is_infinity = _html_bool(form.get("is_infinity"))

        try:
            item_id = int(item_id_raw)
        except (TypeError, ValueError):
            message = "Selecciona un producto valido."
        else:
            entries, invalid_lines = _parse_bulk_account_lines(str(raw_lines))
            created = 0
            batch_duplicates = 0
            db_duplicates = 0
            seen: set[tuple[str, str, str]] = set()
            created_item_name: str | None = None

            async with Database().session() as s:
                goods = (await s.execute(
                    select(Goods).where(Goods.id == item_id)
                )).scalars().first()
                if not goods:
                    message = "El producto seleccionado no existe."
                else:
                    created_item_name = goods.name
                    for entry in entries:
                        fingerprint = (entry["username"] or "", entry["password"] or "", entry["url"] or "")
                        if fingerprint in seen:
                            batch_duplicates += 1
                            continue
                        seen.add(fingerprint)

                        existing = (await s.execute(
                            select(ItemValues.id).where(
                                ItemValues.item_id == item_id,
                                ItemValues.account_username == entry["username"],
                                ItemValues.account_password == entry["password"],
                                ItemValues.account_url == entry["url"],
                            )
                        )).scalar()
                        if existing:
                            db_duplicates += 1
                            continue

                        s.add(ItemValues(
                            item_id=item_id,
                            value=_compose_account_value(
                                entry["username"], entry["password"], entry["url"], entry["value"]
                            ),
                            account_username=entry["username"],
                            account_password=entry["password"],
                            account_url=entry["url"],
                            is_infinity=is_infinity,
                            status="available",
                        ))
                        created += 1
                    message = (
                        f"Cuentas creadas: {created}. "
                        f"Duplicadas en lote: {batch_duplicates}. "
                        f"Duplicadas en base: {db_duplicates}. "
                        f"Invalidas: {len(invalid_lines)}."
                    )
            if created and created_item_name:
                await invalidate_item_cache(created_item_name)
                await invalidate_stats_cache()

    options = "".join(
        f"<option value='{product_id}'>{escape(product_name)}</option>"
        for product_id, product_name in products
    )
    body = f"""
    <p>Pega una cuenta por linea con formato <code>usuario|clave|url</code> o <code>usuario|clave|url|valor_libre</code>.</p>
    <form method="post">
      <label>Producto</label>
      <select name="item_id" required>{options}</select>
      <label class="checkbox"><input type="checkbox" name="is_infinity"> Stock infinito</label>
      <label>Bloque de cuentas</label>
      <textarea name="bulk_lines" placeholder="usuario1|clave1|https://login1.com&#10;usuario2|clave2|https://login2.com" required></textarea>
      <button type="submit">Cargar cuentas</button>
    </form>
    """
    return _render_tools_page("Bulk de cuentas en producto existente", body, message=message, embedded=bool(request.query_params.get("embed")))


async def bulk_unique_products(request: Request) -> HTMLResponse:
    auth_redirect = _ensure_tools_auth(request)
    if auth_redirect:
        return auth_redirect

    message = ""
    async with Database().session() as s:
        categories = (await s.execute(
            select(Categories.id, Categories.name).order_by(Categories.name.asc())
        )).all()

    if request.method == "POST":
        form = await request.form()
        category_id_raw = (form.get("category_id") or "").strip()
        base_name = (form.get("base_name") or "").strip()
        description = (form.get("description") or "").strip()
        raw_lines = form.get("bulk_lines") or ""

        try:
            category_id = int(category_id_raw)
            price = Decimal((form.get("price") or "").strip())
            duration_days = max(int((form.get("duration_days") or "30").strip()), 1)
        except (TypeError, ValueError, InvalidOperation):
            message = "Revisa categoria, precio y duracion."
        else:
            is_renewable = _html_bool(form.get("is_renewable"))
            is_active = _html_bool(form.get("is_active"))
            is_infinity = _html_bool(form.get("is_infinity"))
            entries, invalid_lines = _parse_bulk_unique_lines(str(raw_lines), base_name)
            created_products = 0
            created_accounts = 0
            duplicate_products = 0
            duplicate_accounts = 0
            seen_products: set[str] = set()
            created_names: list[str] = []

            async with Database().session() as s:
                category_exists = (await s.execute(
                    select(Categories.id).where(Categories.id == category_id)
                )).scalar()
                if not category_exists or not description:
                    message = "La categoria existe y la descripcion es obligatoria."
                else:
                    for entry in entries:
                        product_name = str(entry["product_name"])
                        if product_name in seen_products:
                            duplicate_products += 1
                            continue
                        seen_products.add(product_name)

                        existing_product = (await s.execute(
                            select(Goods).where(Goods.name == product_name)
                        )).scalars().first()
                        if existing_product:
                            duplicate_products += 1
                            continue

                        goods = Goods(
                            name=product_name,
                            description=description,
                            price=price,
                            category_id=category_id,
                            duration_days=duration_days,
                            is_renewable=is_renewable,
                            is_active=is_active,
                        )
                        s.add(goods)
                        await s.flush()
                        created_products += 1

                        existing_account = (await s.execute(
                            select(ItemValues.id).where(
                                ItemValues.item_id == goods.id,
                                ItemValues.account_username == entry["username"],
                                ItemValues.account_password == entry["password"],
                                ItemValues.account_url == entry["url"],
                            )
                        )).scalar()
                        if existing_account:
                            duplicate_accounts += 1
                            continue

                        s.add(ItemValues(
                            item_id=goods.id,
                            value=_compose_account_value(
                                str(entry["username"]), str(entry["password"]), str(entry["url"]), entry["value"]
                            ),
                            account_username=str(entry["username"]),
                            account_password=str(entry["password"]),
                            account_url=str(entry["url"]),
                            is_infinity=is_infinity,
                            status="available",
                        ))
                        created_accounts += 1
                        created_names.append(goods.name)

                    message = (
                        f"Productos creados: {created_products}. "
                        f"Cuentas creadas: {created_accounts}. "
                        f"Productos duplicados: {duplicate_products}. "
                        f"Cuentas duplicadas: {duplicate_accounts}. "
                        f"Invalidas: {len(invalid_lines)}."
                    )
            if created_names:
                for item_name in created_names:
                    await invalidate_item_cache(item_name)
                await invalidate_stats_cache()

    options = "".join(
        f"<option value='{category_id}'>{escape(category_name)}</option>"
        for category_id, category_name in categories
    )
    body = f"""
    <p>Modo pensado para crear productos unicos de golpe.</p>
    <p class="hint">Formato aceptado por linea:</p>
    <ul>
      <li><code>usuario|clave|url</code> y se crea con nombre automatico usando <b>Nombre base</b>.</li>
      <li><code>usuario|clave|url|valor_libre</code> con nombre automatico.</li>
      <li><code>nombre_producto|usuario|clave|url</code> si quieres mandar el nombre en cada linea.</li>
      <li><code>nombre_producto|usuario|clave|url|valor_libre</code>.</li>
    </ul>
    <form method="post">
      <div class="row">
        <div>
          <label>Nombre base</label>
          <input type="text" name="base_name" placeholder="CapCut Premium">
          <div class="hint">Si las lineas no traen nombre, se crean como Nombre base 1, 2, 3...</div>
        </div>
        <div>
          <label>Categoria</label>
          <select name="category_id" required>{options}</select>
        </div>
      </div>
      <label>Descripcion comun</label>
      <textarea name="description" required></textarea>
      <div class="row-3">
        <div>
          <label>Precio</label>
          <input type="text" name="price" value="5.00" required>
        </div>
        <div>
          <label>Duracion dias</label>
          <input type="number" name="duration_days" value="30" min="1" required>
        </div>
        <div>
          <label>Opciones</label>
          <label class="checkbox"><input type="checkbox" name="is_renewable" checked> Renovable</label>
          <label class="checkbox"><input type="checkbox" name="is_active" checked> Activo</label>
          <label class="checkbox"><input type="checkbox" name="is_infinity"> Stock infinito</label>
        </div>
      </div>
      <label>Bloque de productos/cuentas</label>
      <textarea name="bulk_lines" placeholder="usuario1|clave1|https://login1.com&#10;usuario2|clave2|https://login2.com" required></textarea>
      <button type="submit">Crear productos unicos</button>
    </form>
    """
    return _render_tools_page("Bulk de productos unicos", body, message=message, embedded=bool(request.query_params.get("embed")))


async def purchases_dashboard(request: Request) -> HTMLResponse:
    """Mis Compras admin dashboard with status tabs, filters, days countdown."""
    auth_redirect = _ensure_tools_auth(request)
    if auth_redirect:
        return auth_redirect

    q = (request.query_params.get("q") or "").strip()
    tab = (request.query_params.get("tab") or "all").strip()
    try:
        page = max(int(request.query_params.get("page") or "1"), 1)
    except ValueError:
        page = 1
    per_page = 50
    embed = bool(request.query_params.get("embed"))
    embed_param = "&embed=1" if embed else ""
    embed_form = '<input type="hidden" name="embed" value="1">' if embed else ""
    iframe_target = ' target="_top"' if embed else ""

    now = datetime.now(timezone.utc)
    soon = now.replace(microsecond=0)

    async with Database().session() as s:
        # Base counters per tab (independent of pagination/search except q)
        base_filters = []
        if q:
            like = f"%{q}%"
            base_filters.append(
                BoughtGoods.item_name.ilike(like)
                | BoughtGoods.buyer_username_snapshot.ilike(like)
                | BoughtGoods.buyer_first_name_snapshot.ilike(like)
                | BoughtGoods.buyer_email_snapshot.ilike(like)
                | BoughtGoods.buyer_whatsapp_snapshot.ilike(like)
                | BoughtGoods.stock_username.ilike(like)
            )
            # Try matching by exact buyer_id / unique_id when q is numeric
            if q.isdigit():
                from sqlalchemy import or_ as sa_or
                base_filters[-1] = sa_or(
                    base_filters[-1],
                    BoughtGoods.buyer_id == int(q),
                    BoughtGoods.unique_id == int(q),
                )

        from sqlalchemy import func as sa_func, and_ as sa_and, or_ as sa_or

        # Compute tab counters
        async def cnt(extra) -> int:
            stmt = select(sa_func.count(BoughtGoods.id))
            for f in base_filters:
                stmt = stmt.where(f)
            if extra is not None:
                stmt = stmt.where(extra)
            return int((await s.execute(stmt)).scalar() or 0)

        c_all = await cnt(None)
        c_active = await cnt(sa_and(
            BoughtGoods.status == "active",
            sa_or(BoughtGoods.expires_at.is_(None), BoughtGoods.expires_at > now),
        ))
        from datetime import timedelta as _td
        c_expiring = await cnt(sa_and(
            BoughtGoods.status.in_(["active", "expiring"]),
            BoughtGoods.expires_at.is_not(None),
            BoughtGoods.expires_at > now,
            BoughtGoods.expires_at <= now + _td(days=7),
        ))
        c_expired = await cnt(sa_or(
            BoughtGoods.status == "expired",
            sa_and(BoughtGoods.expires_at.is_not(None), BoughtGoods.expires_at <= now),
        ))
        c_cancelled = await cnt(BoughtGoods.status == "cancelled")
        c_renewable = await cnt(BoughtGoods.is_renewable.is_(True))

        # Apply tab filter
        tab_filter = None
        if tab == "active":
            tab_filter = sa_and(
                BoughtGoods.status == "active",
                sa_or(BoughtGoods.expires_at.is_(None), BoughtGoods.expires_at > now),
            )
        elif tab == "expiring":
            tab_filter = sa_and(
                BoughtGoods.status.in_(["active", "expiring"]),
                BoughtGoods.expires_at.is_not(None),
                BoughtGoods.expires_at > now,
                BoughtGoods.expires_at <= now + _td(days=7),
            )
        elif tab == "expired":
            tab_filter = sa_or(
                BoughtGoods.status == "expired",
                sa_and(BoughtGoods.expires_at.is_not(None), BoughtGoods.expires_at <= now),
            )
        elif tab == "cancelled":
            tab_filter = BoughtGoods.status == "cancelled"
        elif tab == "renewable":
            tab_filter = BoughtGoods.is_renewable.is_(True)

        stmt = select(BoughtGoods)
        for f in base_filters:
            stmt = stmt.where(f)
        if tab_filter is not None:
            stmt = stmt.where(tab_filter)
        stmt = stmt.order_by(BoughtGoods.id.desc()).limit(per_page).offset((page - 1) * per_page)

        purchases = (await s.execute(stmt)).scalars().all()

        # Aggregate header stats (always full unfiltered)
        total_revenue = (await s.execute(select(sa_func.sum(BoughtGoods.price)))).scalar() or 0

    def day_circle_class(d: int | None, status: str, has_expiry: bool) -> str:
        if status == "cancelled":
            return "dead"
        if not has_expiry:
            return "ok"
        if d is None or d <= 0:
            return "bad"
        if d <= 7:
            return "warn"
        return "ok"

    def status_badge(p: BoughtGoods, d_left: int | None) -> str:
        if p.status == "cancelled":
            return '<span class="badge badge-cancelled">Cancelado</span>'
        if p.status == "expired" or (d_left is not None and d_left <= 0 and p.expires_at is not None):
            return '<span class="badge badge-expired">Vencido</span>'
        if d_left is not None and d_left <= 7:
            return '<span class="badge badge-expiring">Por vencer</span>'
        return '<span class="badge badge-active">Activo</span>'

    def actions_html(p: BoughtGoods, d_left: int | None) -> str:
        # Renew: only sensible if renewable and (expiring soon or already expired)
        is_expired_or_soon = (
            p.status in {"expired", "expiring"}
            or (d_left is not None and d_left <= 7 and p.expires_at is not None)
        )
        if p.is_renewable and is_expired_or_soon and p.status != "cancelled":
            renew = f'<a class="a-renew" href="/admin/bought-goods/edit/{p.id}"{iframe_target} title="Renovar">🔄</a>'
        else:
            renew = '<span class="a-renew a-disabled" title="No renovable" style="display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:8px;background:#f1f5f9;color:#cbd5e1">🔄</span>'

        # Support: WhatsApp first, then email, else view buyer in admin
        if p.buyer_whatsapp_snapshot:
            wa = "".join(c for c in p.buyer_whatsapp_snapshot if c.isdigit())
            support = f'<a class="a-support" href="https://wa.me/{wa}" target="_blank" title="WhatsApp">💬</a>'
        elif p.buyer_email_snapshot:
            support = f'<a class="a-support" href="mailto:{escape(p.buyer_email_snapshot)}" title="Email">📧</a>'
        elif p.buyer_id:
            support = f'<a class="a-support" href="https://t.me/{p.buyer_id}" target="_blank" title="Telegram">✈️</a>'
        else:
            support = '<span class="a-support a-disabled" title="Sin contacto" style="display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:8px;background:#f1f5f9;color:#cbd5e1">💬</span>'

        view = f'<a class="a-view" href="/admin/bought-goods/details/{p.id}"{iframe_target} title="Ver detalle">👁</a>'
        return f'<div class="actions">{renew}{support}{view}</div>'

    rows_html_parts: list[str] = []
    for p in purchases:
        d_left = days_left(p.expires_at)
        cliente = ""
        if p.buyer_username_snapshot:
            cliente = f"@{escape(p.buyer_username_snapshot)}"
        elif p.buyer_first_name_snapshot:
            cliente = escape(p.buyer_first_name_snapshot)
        else:
            cliente = "—"
        contact_bits: list[str] = []
        if p.buyer_email_snapshot:
            contact_bits.append(escape(p.buyer_email_snapshot))
        if p.buyer_whatsapp_snapshot:
            contact_bits.append(escape(p.buyer_whatsapp_snapshot))
        contact_html = "<br>".join(contact_bits) if contact_bits else '<span class="muted">—</span>'

        cuenta_user = escape(p.stock_username) if p.stock_username else '<span class="muted">—</span>'
        cuenta_url = f'<a href="{escape(p.stock_url)}" target="_blank">{escape(p.stock_url)}</a>' if p.stock_url else '<span class="muted">—</span>'
        days_str = days_left_str(p.expires_at)
        d_class = day_circle_class(d_left, p.status, p.expires_at is not None)
        days_chip = f'<span class="day-circle {d_class}">{days_str}</span>'

        rows_html_parts.append(
            "<tr>"
            f"<td>{p.id}</td>"
            f"<td><b>{escape(p.item_name)}</b><div class='muted'>{p.duration_days} dias</div></td>"
            f"<td>{cliente}<div class='muted'>{p.buyer_id or '—'}</div></td>"
            f"<td>{contact_html}</td>"
            f"<td>{cuenta_user}</td>"
            f"<td style='max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>{cuenta_url}</td>"
            f"<td><b>{p.price}</b></td>"
            f"<td>{escape(format_dt(p.bought_datetime))}</td>"
            f"<td>{escape(format_date(p.expires_at))}</td>"
            f"<td style='text-align:center'>{days_chip}</td>"
            f"<td>{status_badge(p, d_left)}</td>"
            f"<td><code>{p.unique_id}</code></td>"
            f"<td>{actions_html(p, d_left)}</td>"
            "</tr>"
        )
    rows_html = "".join(rows_html_parts) or "<tr><td colspan='13' class='muted'>No hay compras que coincidan con el filtro.</td></tr>"

    def tab_link(label: str, key: str, count: int) -> str:
        is_active = tab == key or (key == "all" and tab not in {"active", "expiring", "expired", "cancelled", "renewable"})
        active_cls = "active" if is_active else ""
        href = f"/tools/purchases?tab={key}&q={escape(q)}{embed_param}"
        return f'<a class="t-{key} {active_cls}" href="{href}">{escape(label)} <span class="count">{count}</span></a>'

    tabs_html = (
        '<div class="tabs">'
        f'{tab_link("Todas", "all", c_all)}'
        f'{tab_link("Activas", "active", c_active)}'
        f'{tab_link("Por vencer (7d)", "expiring", c_expiring)}'
        f'{tab_link("Vencidas", "expired", c_expired)}'
        f'{tab_link("Canceladas", "cancelled", c_cancelled)}'
        f'{tab_link("Renovables", "renewable", c_renewable)}'
        '</div>'
    )

    # Pagination links
    has_more = len(purchases) == per_page
    pagination_parts = []
    if page > 1:
        pagination_parts.append(f'<a href="/tools/purchases?tab={tab}&q={escape(q)}&page={page - 1}{embed_param}">« Anterior</a>')
    pagination_parts.append(f'<span class="muted">Pagina {page}</span>')
    if has_more:
        pagination_parts.append(f'<a href="/tools/purchases?tab={tab}&q={escape(q)}&page={page + 1}{embed_param}">Siguiente »</a>')
    pagination_html = ' &nbsp; '.join(pagination_parts)

    body = f"""
    <div class="header-bar">
      <span class="total-pill">💰 ${total_revenue} {EnvKeys.PAY_CURRENCY}</span>
      <span class="muted">Mostrando {len(purchases)} compras en la pagina actual.</span>
    </div>
    <p class="hint">Filtra por estado con las pills de arriba o usa la barra para buscar por producto, cliente, email, WhatsApp, ID Telegram o pedido. La columna <b>Dias</b> muestra el tiempo restante con codigo de color.</p>
    {tabs_html}
    <form method="get" class="filters">
      {embed_form}
      <input type="hidden" name="tab" value="{escape(tab)}">
      <input type="text" name="q" placeholder="🔍 Buscar producto, cliente, email, whatsapp, ID Telegram, pedido..." value="{escape(q)}" style="min-width:380px">
      <button type="submit">Buscar</button>
      <a href="/tools/purchases?tab={escape(tab)}{embed_param}" style="margin-left:6px;color:#475569;text-decoration:none;align-self:center">Limpiar</a>
    </form>
    <table class="compact">
      <thead>
        <tr>
          <th>ID</th>
          <th>Producto</th>
          <th>Cliente</th>
          <th>Contacto</th>
          <th>Usuario cuenta</th>
          <th>URL cuenta</th>
          <th>Precio</th>
          <th>Fecha de inicio</th>
          <th>Vence</th>
          <th style="text-align:center">Dias</th>
          <th>Estado</th>
          <th>Pedido</th>
          <th>Opciones</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
    <div style="margin-top:14px;display:flex;gap:14px;align-items:center">{pagination_html}</div>
    """
    return _render_tools_page("🛒 Mis Compras", body, embedded=bool(request.query_params.get("embed")))


def _embed(view: "BaseView", request: Request, target_url: str, title: str):
    """Render the /tools/* page inside the SQLAdmin layout (sidebar visible).

    Falls back to a plain redirect if the template cannot be rendered.
    """
    sep = "&" if "?" in target_url else "?"
    embed_url = f"{target_url}{sep}embed=1"
    context = {"embed_url": embed_url, "embed_title": title}
    try:
        return view.templates.TemplateResponse(
            request, "sqladmin/embed.html", context,
        )
    except TypeError:
        # Older Starlette signature: TemplateResponse(name, context)
        try:
            return view.templates.TemplateResponse(
                "sqladmin/embed.html",
                {"request": request, **context},
            )
        except Exception:
            return RedirectResponse(target_url, status_code=302)
    except Exception:
        # If the embed template cannot resolve sqladmin/layout.html context,
        # at least redirect so the user still reaches the new dashboard.
        return RedirectResponse(target_url, status_code=302)


class MisComprasSidebar(BaseView):
    """Sidebar entry that renders /tools/purchases inside the admin layout."""
    name = "Mis Compras"
    icon = "fa-solid fa-cart-shopping"

    @expose("/mis-compras", methods=["GET"])
    async def index(self, request: Request):
        return _embed(self, request, "/tools/purchases", "Mis Compras")


class StockSidebar(BaseView):
    name = "Stock"
    icon = "fa-solid fa-warehouse"

    @expose("/stock-dashboard", methods=["GET"])
    async def index(self, request: Request):
        return _embed(self, request, "/tools/stock", "Stock")


class HerramientasSidebar(BaseView):
    name = "Herramientas"
    icon = "fa-solid fa-toolbox"

    @expose("/herramientas", methods=["GET"])
    async def index(self, request: Request):
        return _embed(self, request, "/tools", "Herramientas")


class BulkUniqueSidebar(BaseView):
    name = "Bulk productos unicos"
    icon = "fa-solid fa-bolt"

    @expose("/bulk-unique", methods=["GET"])
    async def index(self, request: Request):
        return _embed(self, request, "/tools/cuentas/bulk-unique", "Bulk productos unicos")


class BulkAccountsSidebar(BaseView):
    name = "Bulk cuentas"
    icon = "fa-solid fa-layer-group"

    @expose("/bulk-cuentas", methods=["GET"])
    async def index(self, request: Request):
        return _embed(self, request, "/tools/cuentas/bulk-existing", "Bulk cuentas")


# App Factory
def create_admin_app() -> Starlette:

    from bot.web.export import export_routes

    routes = [
        Route("/health", health_check),
        Route("/metrics", metrics_json),
        Route("/metrics/prometheus", prometheus_metrics),
        Route("/tools", tools_home),
        Route("/tools/stock", stock_dashboard),
        Route("/tools/purchases", purchases_dashboard),
        Route("/tools/products/new", quick_new_product, methods=["GET", "POST"]),
        Route("/tools/cuentas/bulk-existing", bulk_accounts_existing, methods=["GET", "POST"]),
        Route("/tools/cuentas/bulk-unique", bulk_unique_products, methods=["GET", "POST"]),
    ] + export_routes

    app = Starlette(routes=routes)
    app.add_middleware(SessionMiddleware, secret_key=EnvKeys.SECRET_KEY, max_age=1800)

    auth_backend = AdminAuth(secret_key=EnvKeys.SECRET_KEY)
    admin = Admin(
        app,
        engine=Database().engine,
        authentication_backend=auth_backend,
        title="Telegram Shop Admin",
        templates_dir=_TEMPLATES_DIR,
    )

    # Custom sidebar shortcuts (top of the menu) to the /tools/* dashboards.
    admin.add_view(MisComprasSidebar)
    admin.add_view(StockSidebar)
    admin.add_view(HerramientasSidebar)
    admin.add_view(BulkUniqueSidebar)
    admin.add_view(BulkAccountsSidebar)

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
