from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any, Literal, Union
from enum import Enum
from uuid import UUID

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

# В OpenAPI схема Instrument содержит только name и ticker
class Instrument(BaseModel):
    ticker: str = Field(..., pattern="^[A-Z]{2,10}$")
    name: str

    model_config = {
        "from_attributes": True
    }

# Сохраним расширенную схему для внутреннего использования
class InstrumentDB(BaseModel):
    id: int
    ticker: str
    name: str
    instrument_type: str = "stock"
    commission_rate: Decimal = 0.0
    initial_price: Decimal = 0.0
    available_quantity: int = 0
    is_listed: bool = True

    model_config = {
        "from_attributes": True
    }

class Ok(BaseModel):
    success: bool = True

class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderStatus(str, Enum):
    NEW = "NEW"
    EXECUTED = "EXECUTED"
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
    CANCELLED = "CANCELLED"

class Level(BaseModel):
    price: Decimal
    qty: Decimal

class L2OrderBook(BaseModel):
    bid_levels: List[Level] = Field(..., alias="bids")
    ask_levels: List[Level] = Field(..., alias="asks")
    
    model_config = ConfigDict(populate_by_name=True)

class Transaction(BaseModel):
    ticker: str
    amount: int
    price: int
    timestamp: datetime

class LimitOrderBody(BaseModel):
    direction: Direction
    ticker: str
    qty: int = Field(..., ge=1)
    price: int = Field(..., gt=0)

class MarketOrderBody(BaseModel):
    direction: Direction
    ticker: str
    qty: int = Field(..., ge=1)

class LimitOrder(BaseModel):
    id: str
    status: OrderStatus
    user_id: str
    timestamp: datetime
    body: LimitOrderBody
    filled: int = 0

class MarketOrder(BaseModel):
    id: str
    status: OrderStatus
    user_id: str
    timestamp: datetime
    body: MarketOrderBody

class CreateOrderResponse(BaseModel):
    success: bool = True
    order_id: str

class BalanceOperation(BaseModel):
    user_id: str
    ticker: str
    amount: int = Field(..., gt=0)

# Схемы для инструментов
class InstrumentCreate(Instrument):
    pass

class InstrumentDetails(Instrument):
    instrument_type: str = "stock"
    commission_rate: float = 0.0
    initial_price: float = 0.0
    available_quantity: int = 0
    is_listed: bool = True

    model_config = {
        "from_attributes": True
    }

# Схемы для балансов
class BalanceBase(BaseModel):
    ticker: str
    amount: Decimal

    @field_validator('amount')
    def amount_must_be_positive(cls, v):
        if v < 0:
            raise ValueError('Сумма должна быть положительной')
        return v

class BalanceOut(BalanceBase):
    instrument_name: Optional[str] = None
    
    model_config = {
        "from_attributes": True
    }

class DepositRequest(BaseModel):
    ticker: str
    amount: Decimal
    
    @field_validator('amount')
    def amount_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('Сумма должна быть положительной')
        return v

class WithdrawRequest(BaseModel):
    ticker: str
    amount: Decimal
    
    @field_validator('amount')
    def amount_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('Сумма должна быть положительной')
        return v

class BalanceResponse(BaseModel):
    success: bool
    balance: Optional[Decimal] = None
    message: Optional[str] = None

# Общие
class Ok(BaseModel):
    success: bool = True

# Ордеры
class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"

class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderStatus(str, Enum):
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"

class OrderCreate(BaseModel):
    ticker: str
    side: OrderSide = Field(..., alias="direction")
    quantity: Decimal = Field(..., gt=0, alias="qty")
    price: Optional[Decimal] = Field(None, gt=0)
    
    @property
    def order_type(self) -> OrderType:
        """Автоматически определяет тип ордера на основе наличия цены"""
        return OrderType.LIMIT if self.price is not None else OrderType.MARKET

class OrderOut(BaseModel):
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

# Биржевой стакан
class OrderBookItem(BaseModel):
    price: Decimal
    quantity: Decimal

class OrderBookOut(L2OrderBook):
    pass