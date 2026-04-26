"""Add customer activation, subscription metadata and structured stock.

Revision ID: f1e2d3c4b5a6
Revises: e5f6a7b8c9d0
Create Date: 2026-04-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "f1e2d3c4b5a6"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
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
    if "username" not in user_columns:
        op.add_column("users", sa.Column("username", sa.String(length=64), nullable=True))
    if "first_name" not in user_columns:
        op.add_column("users", sa.Column("first_name", sa.String(length=128), nullable=True))
    if "is_customer_active" not in user_columns:
        op.add_column("users", sa.Column("is_customer_active", sa.Boolean(), nullable=False, server_default=sa.false()))
    user_indexes = _index_names(inspector, "users")
    if "ix_users_username" not in user_indexes:
        op.create_index("ix_users_username", "users", ["username"], unique=False)
    if "ix_users_is_customer_active" not in user_indexes:
        op.create_index("ix_users_is_customer_active", "users", ["is_customer_active"], unique=False)

    goods_columns = _column_names(inspector, "goods")
    if "duration_days" not in goods_columns:
        op.add_column("goods", sa.Column("duration_days", sa.Integer(), nullable=False, server_default="30"))
    if "is_renewable" not in goods_columns:
        op.add_column("goods", sa.Column("is_renewable", sa.Boolean(), nullable=False, server_default=sa.true()))
    if "is_active" not in goods_columns:
        op.add_column("goods", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    goods_indexes = _index_names(inspector, "goods")
    if "ix_goods_is_renewable" not in goods_indexes:
        op.create_index("ix_goods_is_renewable", "goods", ["is_renewable"], unique=False)
    if "ix_goods_is_active" not in goods_indexes:
        op.create_index("ix_goods_is_active", "goods", ["is_active"], unique=False)

    item_value_columns = _column_names(inspector, "item_values")
    if "account_username" not in item_value_columns:
        op.add_column("item_values", sa.Column("account_username", sa.String(length=255), nullable=True))
    if "account_password" not in item_value_columns:
        op.add_column("item_values", sa.Column("account_password", sa.String(length=255), nullable=True))
    if "account_url" not in item_value_columns:
        op.add_column("item_values", sa.Column("account_url", sa.String(length=500), nullable=True))
    if "status" not in item_value_columns:
        op.add_column("item_values", sa.Column("status", sa.String(length=16), nullable=False, server_default="available"))
    if "assigned_at" not in item_value_columns:
        op.add_column("item_values", sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
    if "assigned_user_id" not in item_value_columns:
        op.add_column("item_values", sa.Column("assigned_user_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            "fk_item_values_assigned_user_id_users",
            "item_values",
            "users",
            ["assigned_user_id"],
            ["telegram_id"],
            ondelete="SET NULL",
        )
    item_indexes = _index_names(inspector, "item_values")
    if "ix_item_values_status" not in item_indexes:
        op.create_index("ix_item_values_status", "item_values", ["status"], unique=False)
    if "ix_item_values_assigned_user_id" not in item_indexes:
        op.create_index("ix_item_values_assigned_user_id", "item_values", ["assigned_user_id"], unique=False)

    bought_columns = _column_names(inspector, "bought_goods")
    for name, column in [
        ("stock_username", sa.Column("stock_username", sa.String(length=255), nullable=True)),
        ("stock_password", sa.Column("stock_password", sa.String(length=255), nullable=True)),
        ("stock_url", sa.Column("stock_url", sa.String(length=500), nullable=True)),
        ("starts_at", sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True)),
        ("expires_at", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)),
        ("duration_days", sa.Column("duration_days", sa.Integer(), nullable=False, server_default="0")),
        ("status", sa.Column("status", sa.String(length=16), nullable=False, server_default="active")),
        ("is_renewable", sa.Column("is_renewable", sa.Boolean(), nullable=False, server_default=sa.false())),
        ("expiry_notified", sa.Column("expiry_notified", sa.Boolean(), nullable=False, server_default=sa.false())),
        ("cancelled_at", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True)),
    ]:
        if name not in bought_columns:
            op.add_column("bought_goods", column)
    bought_indexes = _index_names(inspector, "bought_goods")
    if "ix_bought_goods_expires_at" not in bought_indexes:
        op.create_index("ix_bought_goods_expires_at", "bought_goods", ["expires_at"], unique=False)
    if "ix_bought_goods_status" not in bought_indexes:
        op.create_index("ix_bought_goods_status", "bought_goods", ["status"], unique=False)
    if "ix_bought_goods_expiry_notified" not in bought_indexes:
        op.create_index("ix_bought_goods_expiry_notified", "bought_goods", ["expiry_notified"], unique=False)


def downgrade() -> None:
    pass
