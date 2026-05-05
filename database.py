import os
import urllib
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
load_dotenv()

user = os.getenv("DB_USER")
password = os.getenv("DB_PASSWORD")
host = os.getenv("DB_HOST")
port = os.getenv("DB_PORT")
db_name = os.getenv("DB_NAME")
safe_password = urllib.parse.quote_plus(password)

DATABASE_URL = f"postgresql+asyncpg://{user}:{safe_password}@{host}:{port}/{db_name}"


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    # Keep 10 persistent connections open — avoids reconnect overhead on each request
    pool_size=10,
    # Allow up to 20 extra connections under peak load before requests queue
    max_overflow=20,
    # Wait up to 30s for a free connection before raising an error
    pool_timeout=30,
    # Recycle connections every 30 minutes to prevent stale connection errors
    pool_recycle=1800,
    # Verify connection health before use (drops dead connections silently)
    pool_pre_ping=True,
    connect_args={
        "prepared_statement_cache_size": 0,
        "statement_cache_size": 0,
    },
)

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()