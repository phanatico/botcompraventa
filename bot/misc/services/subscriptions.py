import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select

from bot.database.main import Database
from bot.database.models.main import BoughtGoods
from bot.i18n import localize

logger = logging.getLogger(__name__)


class SubscriptionManager:
    """Handle expiry reminders and automatic expiration of purchases."""

    def __init__(self, bot: Bot, interval_seconds: int = 3600):
        self.bot = bot
        self.interval_seconds = interval_seconds
        self.task = None
        self.running = False

    async def start(self):
        self.running = True
        self.task = asyncio.create_task(self._run())
        logger.info("Subscription manager started")

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        logger.info("Subscription manager stopped")

    async def _run(self):
        while self.running:
            try:
                await self.process_expirations()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Subscription manager error: %s", exc, exc_info=True)
            await asyncio.sleep(self.interval_seconds)

    async def process_expirations(self):
        now = datetime.now(timezone.utc)
        notify_before = now + timedelta(days=1)

        notify_payloads = []
        expired_payloads = []

        async with Database().session() as s:
            expiring = (await s.execute(
                select(BoughtGoods).where(
                    BoughtGoods.expires_at.is_not(None),
                    BoughtGoods.status == "active",
                    BoughtGoods.expiry_notified.is_(False),
                    BoughtGoods.expires_at > now,
                    BoughtGoods.expires_at <= notify_before,
                )
            )).scalars().all()

            expired = (await s.execute(
                select(BoughtGoods).where(
                    BoughtGoods.expires_at.is_not(None),
                    BoughtGoods.status.in_(["active", "expiring"]),
                    BoughtGoods.expires_at <= now,
                )
            )).scalars().all()

            for purchase in expiring:
                purchase.status = "expiring"
                purchase.expiry_notified = True
                if purchase.buyer_id:
                    notify_payloads.append({
                        "buyer_id": purchase.buyer_id,
                        "item_name": purchase.item_name,
                        "expires_at": purchase.expires_at,
                        "renewable": purchase.is_renewable,
                    })

            for purchase in expired:
                purchase.status = "expired"
                purchase.cancelled_at = now
                if purchase.buyer_id:
                    expired_payloads.append({
                        "buyer_id": purchase.buyer_id,
                        "item_name": purchase.item_name,
                        "renewable": purchase.is_renewable,
                    })

        for payload in notify_payloads:
            await self._safe_send(
                payload["buyer_id"],
                localize(
                    "subscriptions.expiring_soon",
                    item_name=payload["item_name"],
                    expires_at=payload["expires_at"],
                    renewable=localize("common.yes") if payload["renewable"] else localize("common.no"),
                ),
            )

        for payload in expired_payloads:
            key = "subscriptions.expired_renewable" if payload["renewable"] else "subscriptions.expired_final"
            await self._safe_send(
                payload["buyer_id"],
                localize(key, item_name=payload["item_name"]),
            )

    async def _safe_send(self, user_id: int, text: str):
        try:
            await self.bot.send_message(user_id, text)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("Failed to send subscription notification to %s: %s", user_id, exc)
