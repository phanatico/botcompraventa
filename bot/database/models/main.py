import datetime
from typing import Any

from sqlalchemy import (
    Column, Integer, String, BigInteger, ForeignKey, Text, Boolean,
    DateTime, Numeric, Index, UniqueConstraint, CheckConstraint, func, select
)
from bot.database.main import Database
from sqlalchemy.orm import relationship


class Permission:
    USE             = 1 << 0   #   1 — basic access
    BROADCAST       = 1 << 1   #   2 — mass messaging
    SETTINGS_MANAGE = 1 << 2   #   4 — bot settings (maintenance, etc.)
    USERS_MANAGE    = 1 << 3   #   8 — view/block/unblock users, referrals, purchases
    CATALOG_MANAGE  = 1 << 4   #  16 — categories, positions, items/goods CRUD
    ADMINS_MANAGE   = 1 << 5   #  32 — role CRUD, role assignment
    OWN             = 1 << 6   #  64 — owner-only operations
    STATS_VIEW      = 1 << 7   # 128 — statistics, logs, bought-item search
    BALANCE_MANAGE  = 1 << 8   # 256 — top-up / deduct user balance
    PROMO_MANAGE    = 1 << 9   # 512 — promo code CRUD

    @staticmethod
    def is_subset(perms: int, of: int) -> bool:
        """True if every bit in `perms` is also set in `of`."""
        return (perms & ~of) == 0

    @staticmethod
    def has_any_admin_perm(perms: int) -> bool:
        """True if `perms` has any permission beyond USE."""
        return (perms & ~Permission.USE) != 0


class Role(Database.BASE):
    __tablename__ = 'roles'
    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True)
    default = Column(Boolean, default=False, index=True)
    permissions = Column(Integer)
    users = relationship('User', backref='role', lazy='raise')

    def __init__(self, name: str | None = None, permissions=None, **kwargs):
        super(Role, self).__init__(**kwargs)
        if self.permissions is None:
            self.permissions = 0
        if name is not None:
            self.name = name
        if permissions is not None:
            self.permissions = permissions

    @staticmethod
    async def insert_roles():
        roles = {
            'USER': [Permission.USE],
            'ADMIN': [Permission.USE, Permission.BROADCAST,
                      Permission.SETTINGS_MANAGE, Permission.USERS_MANAGE,
                      Permission.CATALOG_MANAGE, Permission.STATS_VIEW,
                      Permission.BALANCE_MANAGE, Permission.PROMO_MANAGE],
            'OWNER': [Permission.USE, Permission.BROADCAST,
                      Permission.SETTINGS_MANAGE, Permission.USERS_MANAGE,
                      Permission.CATALOG_MANAGE, Permission.ADMINS_MANAGE,
                      Permission.OWN, Permission.STATS_VIEW,
                      Permission.BALANCE_MANAGE, Permission.PROMO_MANAGE],
        }
        default_role = 'USER'
        async with Database().session() as s:
            for r, perms in roles.items():
                result = await s.execute(select(Role).filter_by(name=r))
                role = result.scalars().first()
                if role is None:
                    role = Role(name=r)
                    s.add(role)
                role.reset_permissions()
                for perm in perms:
                    role.add_permission(perm)
                role.default = (role.name == default_role)

    def add_permission(self, perm):
        self.permissions |= perm

    def remove_permission(self, perm):
        self.permissions &= ~perm

    def reset_permissions(self):
        self.permissions = 0

    def has_permission(self, perm):
        return self.permissions & perm == perm

    def __repr__(self):
        return '<Role %r>' % self.name


class User(Database.BASE):
    __tablename__ = 'users'
    telegram_id = Column(BigInteger, primary_key=True)
    role_id = Column(Integer, ForeignKey('roles.id', ondelete="RESTRICT"), default=1, index=True)
    username = Column(String(64), nullable=True, index=True)
    first_name = Column(String(128), nullable=True)
    email = Column(String(255), nullable=True, index=True)
    whatsapp = Column(String(32), nullable=True, index=True)
    balance = Column(Numeric(12, 2), nullable=False, default=0)
    referral_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="SET NULL"), nullable=True, index=True)
    registration_date = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    is_blocked = Column(Boolean, default=False, index=True)
    is_customer_active = Column(Boolean, nullable=False, default=False, index=True)
    user_operations = relationship("Operations", back_populates="user_telegram_id", lazy='raise')
    user_goods = relationship("BoughtGoods", back_populates="user_telegram_id", lazy='raise')

    __table_args__ = (
        CheckConstraint('referral_id != telegram_id', name='ck_users_no_self_referral'),
        Index('ix_users_registration_date', 'registration_date'),
    )

    referral_earnings_received = relationship(
        "ReferralEarnings",
        foreign_keys="ReferralEarnings.referrer_id",
        back_populates="referrer",
        lazy='raise',
    )
    referral_earnings_generated = relationship(
        "ReferralEarnings",
        foreign_keys="ReferralEarnings.referral_id",
        back_populates="referral",
        lazy='raise',
    )

    def __init__(self, telegram_id: int | None = None, registration_date: datetime.datetime | None = None,
                 balance=0, referral_id=None, role_id: int = 1, username: str | None = None,
                 first_name: str | None = None, email: str | None = None, whatsapp: str | None = None,
                 is_customer_active: bool = False, **kw: Any):
        super().__init__(**kw)
        if telegram_id is not None:
            self.telegram_id = telegram_id
        self.role_id = role_id
        if username is not None:
            self.username = username
        if first_name is not None:
            self.first_name = first_name
        if email is not None:
            self.email = email
        if whatsapp is not None:
            self.whatsapp = whatsapp
        self.balance = balance
        if referral_id is not None:
            self.referral_id = referral_id
        if registration_date is not None:
            self.registration_date = registration_date
        self.is_customer_active = is_customer_active


class Categories(Database.BASE):
    __tablename__ = 'categories'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    items = relationship("Goods", back_populates="category", lazy='raise')

    def __init__(self, name: str | None = None, **kw: Any):
        super().__init__(**kw)
        if name is not None:
            self.name = name

    def __str__(self) -> str:
        return self.name or f"Categoria #{self.id}"

    def __repr__(self) -> str:
        return self.__str__()


class Goods(Database.BASE):
    __tablename__ = 'goods'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    price = Column(Numeric(12, 2), nullable=False)
    description = Column(Text, nullable=False)
    duration_days = Column(Integer, nullable=False, default=30)
    is_renewable = Column(Boolean, nullable=False, default=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    category_id = Column(Integer, ForeignKey('categories.id', ondelete="CASCADE"), nullable=False, index=True)
    category = relationship("Categories", back_populates="items", lazy='raise')
    values = relationship("ItemValues", back_populates="item", lazy='raise')

    def __init__(self, name: str | None = None, price=None, description: str | None = None,
                 category_id: int | None = None, duration_days: int = 30,
                 is_renewable: bool = True, is_active: bool = True, **kw: Any):
        super().__init__(**kw)
        if name is not None:
            self.name = name
        if price is not None:
            self.price = price
        if description is not None:
            self.description = description
        self.duration_days = duration_days
        self.is_renewable = is_renewable
        self.is_active = is_active
        if category_id is not None:
            self.category_id = category_id

    def __str__(self) -> str:
        return self.name or f"Producto #{self.id}"

    def __repr__(self) -> str:
        return self.__str__()


class ItemValues(Database.BASE):
    __tablename__ = 'item_values'
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey('goods.id', ondelete="CASCADE"), nullable=False, index=True)
    value = Column(Text, nullable=True)
    account_username = Column(String(255), nullable=True)
    account_password = Column(String(255), nullable=True)
    account_url = Column(String(500), nullable=True)
    is_infinity = Column(Boolean, nullable=False)
    status = Column(String(16), nullable=False, default="available", index=True)
    assigned_at = Column(DateTime(timezone=True), nullable=True)
    assigned_user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="SET NULL"), nullable=True, index=True)
    item = relationship("Goods", back_populates="values", lazy='raise')

    __table_args__ = (
        UniqueConstraint('item_id', 'value', name='uq_item_value_per_item'),
        Index('ix_item_values_item_inf', 'item_id', 'is_infinity'),
    )

    def __init__(self, item_id: int | None = None, value: str | None = None, is_infinity: bool = False,
                 account_username: str | None = None,
                 account_password: str | None = None, account_url: str | None = None, status: str = "available",
                 assigned_user_id: int | None = None, **kw: Any):
        super().__init__(**kw)
        if item_id is not None:
            self.item_id = item_id
        if value is not None:
            self.value = value
        if account_username is not None:
            self.account_username = account_username
        if account_password is not None:
            self.account_password = account_password
        if account_url is not None:
            self.account_url = account_url
        self.is_infinity = is_infinity
        self.status = status
        if assigned_user_id is not None:
            self.assigned_user_id = assigned_user_id

    def __str__(self) -> str:
        username = self.account_username or "sin-usuario"
        return f"Cuenta #{self.id} - {username}"

    def __repr__(self) -> str:
        return self.__str__()


class BoughtGoods(Database.BASE):
    __tablename__ = 'bought_goods'
    id = Column(Integer, primary_key=True)
    item_name = Column(String(100), nullable=False, index=True)
    value = Column(Text, nullable=False)
    stock_username = Column(String(255), nullable=True)
    stock_password = Column(String(255), nullable=True)
    stock_url = Column(String(500), nullable=True)
    buyer_username_snapshot = Column(String(64), nullable=True, index=True)
    buyer_first_name_snapshot = Column(String(128), nullable=True)
    buyer_email_snapshot = Column(String(255), nullable=True, index=True)
    buyer_whatsapp_snapshot = Column(String(32), nullable=True, index=True)
    price = Column(Numeric(12, 2), nullable=False)
    buyer_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="SET NULL"), nullable=True, index=True)
    bought_datetime = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    starts_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    duration_days = Column(Integer, nullable=False, default=0)
    status = Column(String(16), nullable=False, default="active", index=True)
    is_renewable = Column(Boolean, nullable=False, default=False, index=True)
    expiry_notified = Column(Boolean, nullable=False, default=False, index=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    unique_id = Column(BigInteger, nullable=False, unique=True)
    user_telegram_id = relationship("User", back_populates="user_goods", lazy='raise')

    __table_args__ = (
        Index('ix_bought_goods_datetime', 'bought_datetime'),
        Index('ix_bought_goods_buyer_datetime', 'buyer_id', 'bought_datetime'),
    )

    def __init__(self, name: str | None = None, value: str | None = None, price=None, bought_datetime=None,
                 unique_id=None, buyer_id: int = 0, starts_at=None, expires_at=None, duration_days: int = 0,
                 status: str = "active",
                 is_renewable: bool = False, stock_username: str | None = None,
                 stock_password: str | None = None, stock_url: str | None = None,
                 buyer_username_snapshot: str | None = None, buyer_first_name_snapshot: str | None = None,
                 buyer_email_snapshot: str | None = None, buyer_whatsapp_snapshot: str | None = None, **kw: Any):
        super().__init__(**kw)
        if name is not None:
            self.item_name = name
        if value is not None:
            self.value = value
        if stock_username is not None:
            self.stock_username = stock_username
        if stock_password is not None:
            self.stock_password = stock_password
        if stock_url is not None:
            self.stock_url = stock_url
        if buyer_username_snapshot is not None:
            self.buyer_username_snapshot = buyer_username_snapshot
        if buyer_first_name_snapshot is not None:
            self.buyer_first_name_snapshot = buyer_first_name_snapshot
        if buyer_email_snapshot is not None:
            self.buyer_email_snapshot = buyer_email_snapshot
        if buyer_whatsapp_snapshot is not None:
            self.buyer_whatsapp_snapshot = buyer_whatsapp_snapshot
        if price is not None:
            self.price = price
        self.buyer_id = buyer_id
        if bought_datetime is not None:
            self.bought_datetime = bought_datetime
        if starts_at is not None:
            self.starts_at = starts_at
        elif bought_datetime is not None:
            self.starts_at = bought_datetime
        if expires_at is not None:
            self.expires_at = expires_at
        self.duration_days = duration_days
        self.status = status
        self.is_renewable = is_renewable
        if unique_id is not None:
            self.unique_id = unique_id


class Operations(Database.BASE):
    __tablename__ = 'operations'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="SET NULL"), nullable=True, index=True)
    operation_value = Column(Numeric(12, 2), nullable=False)
    operation_time = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    user_telegram_id = relationship("User", back_populates="user_operations", lazy='raise')

    __table_args__ = (
        Index('ix_operations_time', 'operation_time'),
    )

    def __init__(self, user_id: int | None = None, operation_value=None, operation_time=None, **kw: Any):
        super().__init__(**kw)
        if user_id is not None:
            self.user_id = user_id
        if operation_value is not None:
            self.operation_value = operation_value
        if operation_time is not None:
            self.operation_time = operation_time


class Payments(Database.BASE):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    provider = Column(String(32), nullable=False, index=True)
    external_id = Column(String(128), nullable=False)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="SET NULL"), nullable=True, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(8), nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('provider', 'external_id', name='uq_payment_provider_ext'),
        Index('ix_payments_status_created', 'status', 'created_at'),
    )


class ReferralEarnings(Database.BASE):
    __tablename__ = 'referral_earnings'

    id = Column(Integer, primary_key=True)
    referrer_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="CASCADE"), nullable=False, index=True)
    referral_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="CASCADE"), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    original_amount = Column(Numeric(12, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    referrer = relationship(
        "User",
        foreign_keys="ReferralEarnings.referrer_id",
        back_populates="referral_earnings_received",
        lazy='raise',
    )
    referral = relationship(
        "User",
        foreign_keys="ReferralEarnings.referral_id",
        back_populates="referral_earnings_generated",
        lazy='raise',
    )

    __table_args__ = (
        CheckConstraint('referrer_id != referral_id', name='ck_referral_earnings_no_self_referral'),
        Index('ix_referral_earnings_referrer_created', 'referrer_id', 'created_at'),
        Index('ix_referral_earnings_referral_created', 'referral_id', 'created_at'),
    )

    def __init__(self, referrer_id: int | None = None, referral_id: int | None = None,
                 amount=None, original_amount=None, **kw: Any):
        super().__init__(**kw)
        if referrer_id is not None:
            self.referrer_id = referrer_id
        if referral_id is not None:
            self.referral_id = referral_id
        if amount is not None:
            self.amount = amount
        if original_amount is not None:
            self.original_amount = original_amount


class AuditLog(Database.BASE):
    __tablename__ = 'audit_log'
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    level = Column(String(8), nullable=False, default="INFO")
    user_id = Column(BigInteger, nullable=True)
    action = Column(String(64), nullable=False)
    resource_type = Column(String(32), nullable=True)
    resource_id = Column(String(128), nullable=True)
    details = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)

    __table_args__ = (
        Index('ix_audit_log_timestamp', 'timestamp'),
        Index('ix_audit_log_user_id', 'user_id'),
        Index('ix_audit_log_action', 'action'),
    )

    def __repr__(self):
        return f'<AuditLog {self.action} user={self.user_id} @ {self.timestamp}>'


class PromoCodes(Database.BASE):
    __tablename__ = 'promo_codes'
    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    discount_type = Column(String(10), nullable=False)  # 'percent' | 'fixed'
    discount_value = Column(Numeric(12, 2), nullable=False)
    max_uses = Column(Integer, nullable=False, default=0)  # 0 = unlimited
    current_uses = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    category_id = Column(Integer, ForeignKey('categories.id', ondelete='SET NULL'), nullable=True)
    item_id = Column(Integer, ForeignKey('goods.id', ondelete='SET NULL'), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PromoCodeUsages(Database.BASE):
    __tablename__ = 'promo_code_usages'
    id = Column(Integer, primary_key=True)
    promo_id = Column(Integer, ForeignKey('promo_codes.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint('promo_id', 'user_id', name='uq_promo_usage_per_user'),)


class CartItems(Database.BASE):
    __tablename__ = 'cart_items'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False, index=True)
    item_name = Column(String(100), nullable=False)
    promo_code = Column(String(50), nullable=True)
    added_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())



class Reviews(Database.BASE):
    __tablename__ = 'reviews'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False, index=True)
    item_name = Column(String(100), nullable=False, index=True)
    rating = Column(Integer, nullable=False)  # 1-5
    text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint('user_id', 'item_name', name='uq_review_per_user_item'),
        CheckConstraint('rating >= 1 AND rating <= 5', name='ck_review_rating_range'),
    )


async def register_models():
    async with Database().engine.begin() as conn:
        await conn.run_sync(Database.BASE.metadata.create_all)
    await Role.insert_roles()
