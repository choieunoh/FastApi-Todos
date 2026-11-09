"""Alembic 실행 환경 (7.0.0).

- 접속 주소는 환경변수 DATABASE_URL 에서만 읽는다(alembic.ini 에 비밀번호를 두지 않는다). 없으면 멈춘다.
- 대상 스키마는 db.Base.metadata 다(autogenerate 비교용). 리비전은 손으로 검토해서 쓴다.
- 오프라인 모드(`alembic upgrade head --sql`)는 DB 에 연결하지 않고 SQL 만 출력한다.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

import db

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = db.Base.metadata


def run_migrations_offline() -> None:
    # DB 연결 없이 SQL 만 만든다
    context.configure(
        url=db.database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # 연결은 마이그레이션 한 번에만 쓰고 닫는다 (NullPool)
    engine = db.make_engine(db.database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
