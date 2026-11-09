"""로그인·가입 시도 제한 기록 — auth_failures (7.0.0 Phase 2-A)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-05

- 새 테이블과 그 인덱스만 만든다. 0001 의 테이블·열·제약·데이터는 바꾸지 않는다.
- users 와 외래키로 잇지 않는다 (없는 계정에 대한 시도도 기록한다). 기록은 24시간 뒤 정리한다(Phase 2-D).
- 제약 이름은 db.NAMING_CONVENTION 이 만드는 이름과 같다. op.f() 로 감싸 규칙이 한 번 더 붙지 않게 한다.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

import db

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_failures",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("username_key", sa.String(30), nullable=True),
        sa.Column("client_ip", sa.String(45), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_failures")),
        sa.CheckConstraint("kind IN ('login', 'register')", name=op.f("ck_auth_failures_kind")),
        sa.CheckConstraint("kind = 'login' OR username_key IS NULL",
                           name=op.f("ck_auth_failures_register_has_no_username")),
    )
    op.create_index(op.f("ix_auth_failures_kind_username_key_created_at"), "auth_failures",
                    ["kind", "username_key", "created_at"])
    op.create_index(op.f("ix_auth_failures_kind_client_ip_created_at"), "auth_failures",
                    ["kind", "client_ip", "created_at"])
    op.create_index(op.f("ix_auth_failures_created_at"), "auth_failures", ["created_at"])


def downgrade() -> None:
    # 테스트 DB 가 아니면 승인 절차(확인값 2개) 없이는 멈춘다 — 반드시 첫 줄
    db.guard_downgrade(op.get_bind(), context.get_x_argument(as_dictionary=True),
                       offline=context.is_offline_mode())
    # auth_failures 관련 객체만 지운다
    op.drop_index(op.f("ix_auth_failures_created_at"), table_name="auth_failures")
    op.drop_index(op.f("ix_auth_failures_kind_client_ip_created_at"), table_name="auth_failures")
    op.drop_index(op.f("ix_auth_failures_kind_username_key_created_at"), table_name="auth_failures")
    op.drop_table("auth_failures")
