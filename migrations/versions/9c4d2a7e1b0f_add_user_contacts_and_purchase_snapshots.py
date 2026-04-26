"""Add user contacts and purchase snapshots.

Revision ID: 9c4d2a7e1b0f
Revises: f1e2d3c4b5a6
Create Date: 2026-04-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision: str = "9c4d2a7e1b0f"
down_revision: Union[str, None] = "f1e2d3c4b5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(inspector, table_name: str) -> set[str]:
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(inspector, table_name: str) -> set[str]:
    if table_name not in inspector.get_table_names():
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    user_columns = _column_names(inspector, "users")
    if "email" not in user_columns:
        op.add_column("users", sa.Column("email", sa.String(length=255), nullable=True))
    if "whatsapp" not in user_columns:
        op.add_column("users", sa.Column("whatsapp", sa.String(length=32), nullable=True))

    user_indexes = _index_names(inspector, "users")
    if "ix_users_email" not in user_indexes:
        op.create_index("ix_users_email", "users", ["email"], unique=False)
    if "ix_users_whatsapp" not in user_indexes:
        op.create_index("ix_users_whatsapp", "users", ["whatsapp"], unique=False)

    purchase_columns = _column_names(inspector, "bought_goods")
    for name, column in [
        ("buyer_username_snapshot", sa.Column("buyer_username_snapshot", sa.String(length=64), nullable=True)),
        ("buyer_first_name_snapshot", sa.Column("buyer_first_name_snapshot", sa.String(length=128), nullable=True)),
        ("buyer_email_snapshot", sa.Column("buyer_email_snapshot", sa.String(length=255), nullable=True)),
        ("buyer_whatsapp_snapshot", sa.Column("buyer_whatsapp_snapshot", sa.String(length=32), nullable=True)),
    ]:
        if name not in purchase_columns:
            op.add_column("bought_goods", column)

    purchase_indexes = _index_names(inspector, "bought_goods")
    if "ix_bought_goods_buyer_username_snapshot" not in purchase_indexes:
        op.create_index(
            "ix_bought_goods_buyer_username_snapshot",
            "bought_goods",
            ["buyer_username_snapshot"],
            unique=False,
        )
    if "ix_bought_goods_buyer_email_snapshot" not in purchase_indexes:
        op.create_index(
            "ix_bought_goods_buyer_email_snapshot",
            "bought_goods",
            ["buyer_email_snapshot"],
            unique=False,
        )
    if "ix_bought_goods_buyer_whatsapp_snapshot" not in purchase_indexes:
        op.create_index(
            "ix_bought_goods_buyer_whatsapp_snapshot",
            "bought_goods",
            ["buyer_whatsapp_snapshot"],
            unique=False,
        )

    bind.execute(text("""
        UPDATE bought_goods AS bg
        SET
            buyer_username_snapshot = COALESCE(bg.buyer_username_snapshot, u.username),
            buyer_first_name_snapshot = COALESCE(bg.buyer_first_name_snapshot, u.first_name),
            buyer_email_snapshot = COALESCE(bg.buyer_email_snapshot, u.email),
            buyer_whatsapp_snapshot = COALESCE(bg.buyer_whatsapp_snapshot, u.whatsapp)
        FROM users AS u
        WHERE bg.buyer_id = u.telegram_id
    """))


def downgrade() -> None:
    pass
