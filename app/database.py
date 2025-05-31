from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

SQLALCHEMY_DATABASE_URL = "postgresql://birzha:123@localhost/postgres"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=20,         # размер пула соединений
    max_overflow=30,      # запас соединений сверх пула
    pool_timeout=30       # таймаут ожидания соединения (сек)
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()