"""초기 스키마 — 사용자·세션·할 일·하위 할 일·작업 템플릿·이전 기록 (7.0.0)

Revision ID: 0001
Revises:
Create Date: 2026-11-05

- 제약 이름은 db.NAMING_CONVENTION 이 만드는 이름과 같다. op.f() 로 감싸 규칙이 한 번 더 붙지 않게 한다.
- 로그인 시도 제한 테이블(auth_failures)은 Phase 2 리비전에서 따로 만든다.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

import db

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRIORITY_CHECK = "priority IN ('높음', '보통', '낮음')"
RECURRENCE_CHECK = "recurrence IN ('none', 'daily', 'weekly', 'monthly')"
EMPTY_TEXT_ARRAY = sa.text("'{}'::text[]")
FALSE = sa.text("false")


def tz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("username", sa.String(30), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", tz(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )

    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("created_at", tz(), nullable=False),
        sa.Column("expires_at", tz(), nullable=False),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_sessions")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"])
    op.create_index(op.f("ix_sessions_expires_at"), "sessions", ["expires_at"])

    op.create_table(
        "todos",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), server_default="", nullable=False),
        sa.Column("completed", sa.Boolean(), server_default=FALSE, nullable=False),
        sa.Column("priority", sa.String(2), server_default="보통", nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), server_default=EMPTY_TEXT_ARRAY, nullable=False),
        sa.Column("favorite", sa.Boolean(), server_default=FALSE, nullable=False),
        sa.Column("recurrence", sa.String(7), server_default="none", nullable=False),
        sa.Column("next_spawned", sa.Boolean(), server_default=FALSE, nullable=False),
        sa.Column("created_at", tz(), nullable=True),
        sa.Column("completed_at", tz(), nullable=True),
        sa.Column("deleted_at", tz(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_todos")),
        sa.CheckConstraint(PRIORITY_CHECK, name=op.f("ck_todos_priority")),
        sa.CheckConstraint(RECURRENCE_CHECK, name=op.f("ck_todos_recurrence")),
        sa.CheckConstraint("recurrence = 'none' OR due_date IS NOT NULL",
                           name=op.f("ck_todos_recurrence_needs_due_date")),
        sa.UniqueConstraint("user_id", "position", name=op.f("uq_todos_user_id_position")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_todos_user_id_users"), ondelete="CASCADE"),
    )

    op.create_table(
        "subtasks",
        sa.Column("todo_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("completed", sa.Boolean(), server_default=FALSE, nullable=False),
        sa.PrimaryKeyConstraint("todo_id", "id", name=op.f("pk_subtasks")),
        sa.ForeignKeyConstraint(["todo_id"], ["todos.id"], name=op.f("fk_subtasks_todo_id_todos"), ondelete="CASCADE"),
    )

    op.create_table(
        "task_templates",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), server_default="", nullable=False),
        sa.Column("priority", sa.String(2), server_default="보통", nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), server_default=EMPTY_TEXT_ARRAY, nullable=False),
        sa.Column("recurrence", sa.String(7), server_default="none", nullable=False),
        sa.Column("subtasks", postgresql.ARRAY(sa.Text()), server_default=EMPTY_TEXT_ARRAY, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_templates")),
        sa.CheckConstraint(PRIORITY_CHECK, name=op.f("ck_task_templates_priority")),
        sa.CheckConstraint(RECURRENCE_CHECK, name=op.f("ck_task_templates_recurrence")),
        sa.UniqueConstraint("user_id", "position", name=op.f("uq_task_templates_user_id_position")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_task_templates_user_id_users"),
                                ondelete="CASCADE"),
    )
    # 템플릿 이름은 같은 사용자 안에서 대소문자 무시로 고유하다
    op.create_index(op.f("uq_task_templates_user_id_lower_name"), "task_templates",
                    ["user_id", sa.text("lower(name)")], unique=True)

    op.create_table(
        "data_migrations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("source_sha256", sa.CHAR(64), nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("todo_count", sa.Integer(), nullable=False),
        sa.Column("template_count", sa.Integer(), nullable=False),
        sa.Column("migrated_at", tz(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_migrations")),
        sa.UniqueConstraint("source_sha256", name=op.f("uq_data_migrations_source_sha256")),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name=op.f("fk_data_migrations_owner_user_id_users")),
    )


def downgrade() -> None:
    # 모든 테이블을 지우므로 테스트 DB 가 아니면 승인 절차(확인값 2개) 없이는 멈춘다 — 반드시 첫 줄
    db.guard_downgrade(op.get_bind(), context.get_x_argument(as_dictionary=True),
                       offline=context.is_offline_mode())
    # 만든 순서의 반대로 지운다 (외래키 때문에)
    op.drop_table("data_migrations")
    op.drop_index(op.f("uq_task_templates_user_id_lower_name"), table_name="task_templates")
    op.drop_table("task_templates")
    op.drop_table("subtasks")
    op.drop_table("todos")
    op.drop_index(op.f("ix_sessions_expires_at"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("users")
