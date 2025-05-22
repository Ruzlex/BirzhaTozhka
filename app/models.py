import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Numeric, Integer, ForeignKey, Float, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from uuid import uuid4
from .database import Base
import datetime
import enum

class OrderType(str, enum.Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"

class OrderSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderStatus(str, enum.Enum):
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid4()))
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="USER")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    api_key = Column(String, unique=True, index=True, nullable=False)

    # Отношения
    balances = relationship("Balance", back_populates="user")
    orders = relationship("Order", back_populates="user")

class Instrument(Base):
    __tablename__ = "instruments"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    instrument_type = Column(String, nullable=False, default="stock")
    commission_rate = Column(Float, default=0.0)
    initial_price = Column(Float, default=0.0)
    available_quantity = Column(Integer, default=0)
    is_listed = Column(Boolean, default=True)

    # Отношения
    balances = relationship("Balance", back_populates="instrument")
    orders = relationship("Order", foreign_keys="Order.instrument_id", back_populates="instrument")

class Balance(Base):
    __tablename__ = "balances"

    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    ticker = Column(String, ForeignKey("instruments.ticker"), nullable=False)
    amount = Column(Numeric(precision=18, scale=8), nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    # Отношения
    user = relationship("User", back_populates="balances")
    instrument = relationship("Instrument", back_populates="balances")

class Order(Base):
    __tablename__ = "orders"

    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), nullable=False)
    ticker = Column(String, ForeignKey("instruments.ticker"), nullable=False)
    
    order_type = Column(Enum(OrderType), nullable=False)
    side = Column(Enum(OrderSide), nullable=False)
    
    quantity = Column(Numeric(precision=18, scale=8), nullable=False)
    price = Column(Numeric(precision=18, scale=8), nullable=True)  # NULL для Market ордеров
    filled_quantity = Column(Numeric(precision=18, scale=8), default=0)
    
    status = Column(Enum(OrderStatus), default=OrderStatus.OPEN)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    # Отношения
    user = relationship("User", back_populates="orders")
    instrument = relationship("Instrument", foreign_keys=[instrument_id], back_populates="orders")
    instrument_by_ticker = relationship("Instrument", foreign_keys=[ticker], viewonly=True)