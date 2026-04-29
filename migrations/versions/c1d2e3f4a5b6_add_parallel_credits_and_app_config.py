"""add parallel credits and app config

Revision ID: c1d2e3f4a5b6
Revises: 9c4d2a7e1b0f
Create Date: 2026-04-29 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = '9c4d2a7e1b0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('credit_balance', sa.Integer(), nullable=False, server_default='0'))
    op.create_index(op.f('ix_users_credit_balance'), 'users', ['credit_balance'], unique=False)

    op.add_column('goods', sa.Column('credit_price', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_goods_credit_price'), 'goods', ['credit_price'], unique=False)

    op.create_table(
        'app_config',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('key')
    )

    op.create_table(
        'credit_movements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=True),
        sa.Column('delta', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=64), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('admin_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['admin_id'], ['users.telegram_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.telegram_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_credit_movements_admin_id'), 'credit_movements', ['admin_id'], unique=False)
    op.create_index(op.f('ix_credit_movements_created_at'), 'credit_movements', ['created_at'], unique=False)
    op.create_index(op.f('ix_credit_movements_user_id'), 'credit_movements', ['user_id'], unique=False)

    op.execute(
        sa.text(
            "INSERT INTO app_config (key, value) VALUES "
            "('buy_credits_plans', '7|5\n12|10'), "
            "('buy_credits_text', 'PLANES DISPONIBLES\n\n7 USD = 5 creditos\n12 USD = 10 creditos\n\n1 credito = 1 codigo'), "
            "('rules_text', ''), "
            "('menu_motd', ''), "
            "('manual_recharge_text', '') "
            "ON CONFLICT (key) DO NOTHING"
        )
    )

    op.alter_column('users', 'credit_balance', server_default=None)


def downgrade() -> None:
    op.drop_index(op.f('ix_credit_movements_user_id'), table_name='credit_movements')
    op.drop_index(op.f('ix_credit_movements_created_at'), table_name='credit_movements')
    op.drop_index(op.f('ix_credit_movements_admin_id'), table_name='credit_movements')
    op.drop_table('credit_movements')

    op.drop_table('app_config')

    op.drop_index(op.f('ix_goods_credit_price'), table_name='goods')
    op.drop_column('goods', 'credit_price')

    op.drop_index(op.f('ix_users_credit_balance'), table_name='users')
    op.drop_column('users', 'credit_balance')
