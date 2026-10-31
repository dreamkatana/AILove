from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings


def _normalize_database_url(raw_url: str) -> tuple[str, dict]:
    """Normalize Neon.tech / Supabase Postgres URLs for SQLAlchemy + asyncpg."""
    if raw_url.startswith("sqlite"):
        return raw_url, {}

    url = raw_url
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parsed = urlparse(url)
    query_params = dict(parse_qsl(parsed.query))
    connect_args: dict = {}

    # asyncpg uses ssl=True in connect_args instead of ?sslmode=require
    if "sslmode" in query_params:
        sslmode = query_params.pop("sslmode")
        if sslmode in ("require", "verify-ca", "verify-full"):
            connect_args["ssl"] = True
    query_params.pop("channel_binding", None)

    clean_url = urlunparse(parsed._replace(query=urlencode(query_params)))
    return clean_url, connect_args


_db_url, _connect_args = _normalize_database_url(settings.database_url)

# NullPool for serverless Postgres (Neon / Lambda freezes); default pool for SQLite.
engine = create_async_engine(
    _db_url,
    echo=False,
    connect_args=_connect_args,
    poolclass=NullPool if not _db_url.startswith("sqlite") else None,
)

AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    from app import models  # noqa: F401  (register tables)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

