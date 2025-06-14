from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any, Literal, Union
from enum import Enum
from uuid import UUID

class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"

class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

# Статусы для API (используются в схемах)
class OrderStatus(str, Enum):
    NEW = "NEW"
    EXECUTED = "EXECUTED"
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
    CANCELLED = "CANCELLED"

class NewUser(BaseModel):
    name: str = Field(..., min_length=3)

class UserRole(str, Enum):
    USER = "USER"
    ADMIN = "ADMIN"

class UserOut(BaseModel):
    id: str
    name: str
    role: str = "USER"
    api_key: str

    model_config = {
        "from_attributes": True
    }

class Instrument(BaseModel):
    ticker: str = Field(..., pattern="^[A-Z]{2,10}$")
    name: str

    model_config = {
        "from_attributes": True
    }

class InstrumentDB(BaseModel):
    id: int
    ticker: str
    name: str
    instrument_type: str = "stock"
    commission_rate: Decimal = Decimal('0')
    initial_price: Decimal = Decimal('0')
    available_quantity: int = 0
    is_listed: bool = True

    model_config = {
        "from_attributes": True
    }

class Level(BaseModel):
    price: Decimal = Field(..., gt=0)
    qty: Decimal = Field(..., gt=0)

    @field_validator('price')
    def validate_price(cls, v):
        if v <= 0:
            raise ValueError('Цена должна быть положительной')
        return v

    @field_validator('qty')
    def validate_qty(cls, v):
        if v <= 0:
            raise ValueError('Количество должно быть положительным')
        return v

class OrderBookOut(BaseModel):
    bid_levels: List[Level] = Field(default_factory=list)
    ask_levels: List[Level] = Field(default_factory=list)
    
    model_config = ConfigDict(populate_by_name=True)

    @field_validator('bid_levels', 'ask_levels')
    def validate_levels(cls, v):
        if not isinstance(v, list):
            raise ValueError('Уровни должны быть списком')
        return v

class OrderBase(BaseModel):
    id: str
    ticker: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    filled_quantity: Decimal = Decimal('0')
    status: OrderStatus
    created_at: datetime
    updated_at: datetime

    model_config = {
        "from_attributes": True
    }

class LimitOrder(OrderBase):
    price: Decimal

class MarketOrder(OrderBase):
    price: Optional[Decimal] = None

class OrderCreate(BaseModel):
    ticker: str
    side: OrderSide = Field(..., alias="direction")
    quantity: Decimal = Field(..., gt=0, alias="qty")
    price: Optional[Decimal] = Field(None)
    
    @field_validator('price')
    def validate_price(cls, v):
        if v is not None and v <= 0:
            raise ValueError('Цена должна быть положительной')
        return v
    
    @field_validator('quantity')
    def validate_quantity(cls, v):
        if v <= 0:
            raise ValueError('Количество должно быть положительным')
        return v
    
    @property
    def order_type(self) -> OrderType:
        """Автоматически определяет тип ордера на основе наличия цены"""
        return OrderType.LIMIT if self.price is not None else OrderType.MARKET

class OrderOut(OrderBase):
    id: str
    ticker: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Optional[Decimal] = None
    filled_quantity: Decimal
    status: OrderStatus
    created_at: datetime
    updated_at: datetime
    
    model_config = {
        "from_attributes": True
    }

class Transaction(BaseModel):
    ticker: str
    amount: int
    price: int
    timestamp: datetime

class CreateOrderResponse(BaseModel):
    success: bool = True
    order_id: str

class BalanceOperation(BaseModel):
    user_id: str
    ticker: str
    amount: int = Field(..., gt=0)

class Ok(BaseModel):
    success: bool = True