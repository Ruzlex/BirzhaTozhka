from pydantic import BaseModel, EmailStr, Field, field_validator
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

class UserCreate(BaseModel):
    name: str = Field(..., min_length=3)
    email: EmailStr
    password: str = Field(..., min_length=6)

class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class Instrument(BaseModel):
    name: str
    ticker: str = Field(..., regex="^[A-Z]{2,10}$")

class InstrumentDB(Instrument):
    id: int

    model_config = {
        "from_attributes": True
    }

class InstrumentCreate(BaseModel):
    name: str
    ticker: str = Field(..., regex="^[A-Z]{2,10}$")

class InstrumentUpdate(BaseModel):
    name: Optional[str]
    ticker: Optional[str] = Field(None, regex="^[A-Z]{2,10}$")

class BalanceBase(BaseModel):
    ticker: str
    amount: Decimal

class BalanceOut(BalanceBase):
    pass

class DepositRequest(BaseModel):
    user_id: UUID
    ticker: str
    amount: int = Field(..., gt=0)

class WithdrawRequest(BaseModel):
    user_id: UUID
    ticker: str
    amount: int = Field(..., gt=0)

class BalanceResponse(BaseModel):
    __root__: Dict[str, int]

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
    price: int
    qty: int

class L2OrderBook(BaseModel):
    bid_levels: List[Level]
    ask_levels: List[Level]

class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"

class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderCreate(BaseModel):
    type: OrderType
    side: OrderSide
    ticker: str
    qty: int = Field(..., ge=1)
    price: Optional[int] = Field(None, gt=0)

class OrderOut(BaseModel):
    id: UUID
    user_id: UUID
    instrument_id: int
    order_type: OrderType
    side: OrderSide
    quantity: Decimal
    price: Optional[Decimal]
    status: OrderStatus
    filled_quantity: Decimal
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

    model_config = {
        "from_attributes": True
    }

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

class InstrumentCreate(BaseModel):
    name: str
    ticker: str

class InstrumentDetails(InstrumentCreate):
    id: int

class BalanceBase(BaseModel):
    ticker: str
    amount: int

class BalanceOut(BalanceBase):
    pass

class DepositRequest(BaseModel):
    user_id: UUID
    ticker: str
    amount: int

class WithdrawRequest(BaseModel):
    user_id: UUID
    ticker: str
    amount: int

class BalanceResponse(BaseModel):
    __root__: Dict[str, int]

class Ok(BaseModel):
    success: bool = True

class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderStatus(str, Enum):
    NEW = "NEW"
    EXECUTED = "EXECUTED"
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
    CANCELLED = "CANCELLED"

class OrderCreate(BaseModel):
    type: OrderType
    side: OrderSide
    ticker: str
    qty: int
    price: Optional[int]

class OrderOut(BaseModel):
    id: UUID
    user_id: UUID
    instrument_id: int
    order_type: OrderType
    side: OrderSide
    quantity: Decimal
    price: Optional[Decimal]
    status: OrderStatus
    filled_quantity: Decimal
    created_at: datetime
    updated_at: datetime

    model_config = {
        "from_attributes": True
    }

class OrderBookItem(BaseModel):
    price: Decimal
    quantity: Decimal

class OrderBookOut(BaseModel):
    bids: List[OrderBookItem]  # Заявки на покупку (по убыванию цены)
    asks: List[OrderBookItem]  # Заявки на продажу (по возрастанию цены)
