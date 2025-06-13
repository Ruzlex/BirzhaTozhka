from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc
from typing import List, Optional
from decimal import Decimal
from .. import schemas, models
from ..dependencies import get_db, get_current_user, get_current_admin
from uuid import UUID
import datetime

# Публичный роутер для работы со стаканом (не требует авторизации)
router = APIRouter(prefix="/api/v1/public", tags=["public"])

@router.get("/orderbook/{ticker}", response_model=schemas.OrderBookOut)
def get_orderbook(
    ticker: str, 
    limit: int = 10,
    db: Session = Depends(get_db)
):
    """
    Получить текущий биржевой стакан (книгу заявок) для указанного инструмента.
    """
    instrument = db.query(models.Instrument).filter(models.Instrument.ticker == ticker).first()
    if not instrument:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Инструмент с тикером {ticker} не найден"
        )
    
    # Базовый запрос для обоих типов ордеров
    base_query = (
        db.query(models.Order)
        .filter(
            models.Order.ticker == ticker,
            models.Order.order_type == models.OrderType.LIMIT,  # Только лимитные ордера
            models.Order.status == models.OrderStatus.NEW,      # Только NEW ордера
            models.Order.price.isnot(None),                     # Цена должна быть указана
            (models.Order.quantity - models.Order.filled_quantity) > 0  # Должен быть остаток
        )
    )
    
    # Получаем ордера на покупку
    buy_orders = (
        base_query
        .filter(models.Order.side == models.OrderSide.BUY)
        .order_by(desc(models.Order.price), asc(models.Order.created_at))
        .all()
    )

    # Получаем ордера на продажу
    sell_orders = (
        base_query
        .filter(models.Order.side == models.OrderSide.SELL)
        .order_by(asc(models.Order.price), asc(models.Order.created_at))
        .all()
    )

    # Агрегируем объемы по ценам для покупок (BID)
    bid_levels = {}
    for order in buy_orders:
        remaining_qty = order.quantity - order.filled_quantity
        if remaining_qty <= 0:  # Дополнительная проверка
            continue
        if order.price is None:  # Пропускаем ордера без цены
            continue
        price_key = Decimal(str(order.price))  # Конвертируем в Decimal для точности
        if price_key not in bid_levels:
            bid_levels[price_key] = Decimal('0')
        bid_levels[price_key] += remaining_qty

    # Агрегируем объемы по ценам для продаж (ASK)
    ask_levels = {}
    for order in sell_orders:
        remaining_qty = order.quantity - order.filled_quantity
        if remaining_qty <= 0:  # Дополнительная проверка
            continue
        if order.price is None:  # Пропускаем ордера без цены
            continue
        price_key = Decimal(str(order.price))  # Конвертируем в Decimal для точности
        if price_key not in ask_levels:
            ask_levels[price_key] = Decimal('0')
        ask_levels[price_key] += remaining_qty

    # Формируем отсортированные списки уровней
    sorted_bids = [
        schemas.Level(price=price, qty=qty)
        for price, qty in sorted(bid_levels.items(), key=lambda x: (-x[0], -x[1]))
        if qty > 0
    ][:limit]

    sorted_asks = [
        schemas.Level(price=price, qty=qty)
        for price, qty in sorted(ask_levels.items(), key=lambda x: (x[0], -x[1]))
        if qty > 0
    ][:limit]

    return schemas.OrderBookOut(
        bid_levels=sorted_bids,
        ask_levels=sorted_asks
    )

# Защищенный роутер для работы с ордерами (требует авторизации)
protected_router = APIRouter(prefix="/api/v1/order", tags=["order"])

@protected_router.post("", response_model=schemas.CreateOrderResponse)
def create_order(
    order: schemas.OrderCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Создать новую заявку на покупку или продажу.
    
    Тип ордера определяется автоматически по наличию цены:
    - Если цена указана (не NULL), создается лимитный ордер
    - Если цена не указана (NULL), создается рыночный ордер, цена будет определена при исполнении
    """
    # Проверяем, что инструмент существует
    instrument = db.query(models.Instrument).filter(models.Instrument.ticker == order.ticker).first()
    if not instrument:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Инструмент с тикером {order.ticker} не найден"
        )
    
    # Определяем тип ордера на основе наличия цены
    order_type = order.order_type
    
    # Базовая валюта системы - RUB
    rub_instrument = db.query(models.Instrument).filter(models.Instrument.ticker == "RUB").first()
    if not rub_instrument:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Базовая валюта RUB не найдена в системе"
        )
    
    # Проверяем достаточность средств с учетом зарезервированных
    if order.side == schemas.OrderSide.BUY:
        # Находим рублевый баланс пользователя
        rub_balance = db.query(models.Balance).filter(
            models.Balance.user_id == current_user.id,
            models.Balance.ticker == "RUB"
        ).first()
        
        if not rub_balance:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="У вас нет баланса в RUB"
            )
            
        # Получаем сумму, зарезервированную в других ордерах на покупку
        reserved_rub = get_reserved_balance(db, current_user.id, "RUB")
        available_rub = rub_balance.amount - reserved_rub
        
        if available_rub <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Нет доступных средств. Весь баланс зарезервирован в других ордерах."
            )
        
        # Для лимитного ордера проверяем точную сумму
        if order_type == schemas.OrderType.LIMIT:
            required_amount = order.price * order.quantity
            if available_rub < required_amount:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Недостаточно средств. "
                        f"Требуется: {required_amount} RUB, "
                        f"всего на балансе: {rub_balance.amount} RUB, "
                        f"зарезервировано: {reserved_rub} RUB, "
                        f"доступно: {available_rub} RUB"
                    )
                )
        # Для рыночного ордера проверяем возможность исполнения
        else:
            can_execute, error_msg, estimated_cost = check_market_order_executable(
                db, order.ticker, order.side, order.quantity
            )
            if not can_execute:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error_msg
                )
            if available_rub < estimated_cost:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Недостаточно средств для рыночной покупки. "
                        f"Требуется примерно: {estimated_cost} RUB, "
                        f"всего на балансе: {rub_balance.amount} RUB, "
                        f"зарезервировано: {reserved_rub} RUB, "
                        f"доступно: {available_rub} RUB"
                    )
                )

    else:  # SELL
        asset_balance = db.query(models.Balance).filter(
            models.Balance.user_id == current_user.id,
            models.Balance.ticker == order.ticker
        ).first()
        
        if not asset_balance:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"У вас нет баланса в {order.ticker}"
            )
            
        # Получаем сумму, зарезервированную в других ордерах на продажу
        reserved_asset = get_reserved_balance(db, current_user.id, order.ticker)
        available_asset = asset_balance.amount - reserved_asset
        
        if available_asset <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Нет доступных {order.ticker}. Весь баланс зарезервирован в других ордерах."
            )

        if available_asset < order.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Недостаточно {order.ticker}. "
                    f"Требуется: {order.quantity}, "
                    f"всего на балансе: {asset_balance.amount}, "
                    f"зарезервировано: {reserved_asset}, "
                    f"доступно: {available_asset}"
                )
            )
    
    # Создаем новый ордер
    new_order = models.Order(
        user_id=current_user.id,
        instrument_id=instrument.id,
        ticker=order.ticker,
        order_type=order_type,
        side=order.side,
        quantity=order.quantity,
        price=order.price,  # Для рыночного ордера price будет None
        filled_quantity=0,
        status=models.OrderStatus.NEW
    )
    
    db.add(new_order)
    db.commit()
    db.refresh(new_order)
    
    # Резервируем средства
    if order.side == schemas.OrderSide.BUY and order_type == schemas.OrderType.LIMIT:
        # Резервируем рубли для лимитного ордера на покупку
        rub_balance.amount -= order.price * order.quantity
        db.commit()
    elif order.side == schemas.OrderSide.SELL:
        # Резервируем актив при продаже (для любого типа ордера)
        asset_balance.amount -= order.quantity
        db.commit()
    
    # Выполняем матчинг ордера
    try:
        execute_matching(db, new_order.id)
    except ValueError as e:
        cancel_order_and_return_funds(db, new_order.id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        cancel_order_and_return_funds(db, new_order.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Внутренняя ошибка при исполнении ордера: {str(e)}"
        )
    
    # Перезагружаем ордер, чтобы получить актуальный статус после матчинга
    db.refresh(new_order)
    
    return {"success": True, "order_id": new_order.id}

@protected_router.get("", response_model=List[schemas.OrderOut])
def list_orders(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить список всех заявок текущего пользователя.
    """
    orders = db.query(models.Order).filter(models.Order.user_id == current_user.id).all()
    return [
        {
            "id": order.id,
            "ticker": order.ticker,
            "side": order.side,
            "order_type": order.order_type,
            "quantity": order.quantity,
            "price": order.price,
            "filled_quantity": order.filled_quantity,
            "status": convert_db_status_to_api(order.status),
            "created_at": order.created_at,
            "updated_at": order.updated_at
        }
        for order in orders
    ]

@protected_router.get("/{order_id}", response_model=schemas.OrderOut)
def get_order(
    order_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить информацию о конкретной заявке текущего пользователя.
    """
    order = db.query(models.Order).filter(
        models.Order.id == order_id,
        models.Order.user_id == current_user.id
    ).first()
    
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Заявка с ID {order_id} не найдена или не принадлежит текущему пользователю"
        )
    
    # Конвертируем статус перед возвратом
    api_status = convert_db_status_to_api(order.status)
    order_dict = {
        "id": order.id,
        "ticker": order.ticker,
        "side": order.side,
        "order_type": order.order_type,
        "quantity": order.quantity,
        "price": order.price,
        "filled_quantity": order.filled_quantity,
        "status": api_status,
        "created_at": order.created_at,
        "updated_at": order.updated_at
    }
    return order_dict

@protected_router.delete("/{order_id}", response_model=schemas.Ok)
def cancel_order(
    order_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Отменить открытую заявку.
    
    Возвращает зарезервированные средства на баланс пользователя.
    """
    order = db.query(models.Order).filter(
        models.Order.id == order_id,
        models.Order.user_id == current_user.id
    ).first()
    
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Заявка с ID {order_id} не найдена или не принадлежит текущему пользователю"
        )
    
    if order.status != models.OrderStatus.NEW and order.status != models.OrderStatus.PARTIALLY_EXECUTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Невозможно отменить заявку в статусе {order.status}"
        )
    
    # Возвращаем зарезервированные средства
    remaining_quantity = order.quantity - order.filled_quantity

    if remaining_quantity > 0:
        if order.side == models.OrderSide.BUY:
            # Возвращаем рубли только для лимитных ордеров
            if order.order_type == models.OrderType.LIMIT:
                if order.price is None:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Ордер типа LIMIT не содержит цену"
                    )
                rub_balance = db.query(models.Balance).filter(
                    models.Balance.user_id == current_user.id,
                    models.Balance.ticker == "RUB"
                ).first()
                if not rub_balance:
                    rub_balance = models.Balance(
                        user_id=current_user.id,
                        ticker="RUB",
                        amount=0
                    )
                    db.add(rub_balance)
                    db.flush()
                rub_balance.amount += remaining_quantity * order.price

        elif order.side == models.OrderSide.SELL:
            # Возвращаем актив
            asset_balance = db.query(models.Balance).filter(
                models.Balance.user_id == current_user.id,
                models.Balance.ticker == order.ticker
            ).first()
            if not asset_balance:
                asset_balance = models.Balance(
                    user_id=current_user.id,
                    ticker=order.ticker,
                    amount=0
                )
                db.add(asset_balance)
                db.flush()
            asset_balance.amount += remaining_quantity
    
    # Отмечаем ордер как отмененный
    order.status = models.OrderStatus.CANCELLED
    order.updated_at = datetime.datetime.utcnow()
    
    db.commit()
    
    return {"success": True}

# Вспомогательные функции

def execute_matching(db: Session, order_id: str):
    """
    Выполняет матчинг ордера с имеющимися встречными заявками.
    """
    # Загружаем ордер
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise ValueError(f"Ордер с ID {order_id} не найден")
    
    # Проверяем статус ордера
    if order.status != models.OrderStatus.NEW:
        return
    
    # Для рыночных ордеров проверяем наличие встречных заявок
    if order.order_type == models.OrderType.MARKET:
        can_execute, error_msg, _ = check_market_order_executable(
            db, order.ticker, order.side, order.quantity
        )
        if not can_execute:
            raise ValueError(error_msg)
    
    # Ищем встречные ордера
    if order.side == models.OrderSide.BUY:
        # Для покупки ищем ордера на продажу
        counter_orders_query = (
            db.query(models.Order)
            .filter(
                models.Order.ticker == order.ticker,
                models.Order.side == models.OrderSide.SELL,
                models.Order.status.in_([models.OrderStatus.NEW]),
                models.Order.quantity > models.Order.filled_quantity
            )
        )
        
        if order.order_type == models.OrderType.LIMIT:
            # Для лимитного ордера на покупку берем только ордера с ценой <= нашей
            counter_orders_query = counter_orders_query.filter(
                models.Order.price <= order.price
            )
        
        counter_orders = counter_orders_query.order_by(
            asc(models.Order.price),
            asc(models.Order.created_at)
        ).all()
        
    else:  # SELL
        # Для продажи ищем ордера на покупку
        counter_orders_query = (
            db.query(models.Order)
            .filter(
                models.Order.ticker == order.ticker,
                models.Order.side == models.OrderSide.BUY,
                models.Order.status.in_([models.OrderStatus.NEW]),
                models.Order.quantity > models.Order.filled_quantity
            )
        )
        
        if order.order_type == models.OrderType.LIMIT:
            # Для лимитного ордера на продажу берем только ордера с ценой >= нашей
            counter_orders_query = counter_orders_query.filter(
                models.Order.price >= order.price
            )
        
        counter_orders = counter_orders_query.order_by(
            desc(models.Order.price),
            asc(models.Order.created_at)
        ).all()
    
    if order.order_type == models.OrderType.MARKET and not counter_orders:
        raise ValueError("Нет встречных заявок для исполнения рыночного ордера")
    
    # Выполняем матчинг
    for counter_order in counter_orders:
        # Проверяем, не исполнен ли уже наш ордер
        if order.filled_quantity >= order.quantity:
            break
        
        # Пропускаем собственные ордера
        if counter_order.user_id == order.user_id:
            continue
        
        # Определяем объем сделки
        order_remaining = order.quantity - order.filled_quantity
        counter_remaining = counter_order.quantity - counter_order.filled_quantity
        
        if counter_remaining <= 0:
            continue
            
        deal_quantity = min(order_remaining, counter_remaining)
        deal_price = counter_order.price  # Берем цену из встречного ордера
        
        # Выполняем сделку
        execute_deal(db, order, counter_order, deal_quantity, deal_price)
        
        # Обновляем статусы ордеров
        if counter_order.filled_quantity >= counter_order.quantity:
            counter_order.status = models.OrderStatus.EXECUTED
        else:
            counter_order.status = models.OrderStatus.PARTIALLY_EXECUTED
            
    # Завершаем обработку основного ордера
    if order.filled_quantity >= order.quantity:
        order.status = models.OrderStatus.EXECUTED
    elif order.filled_quantity > 0:
        order.status = models.OrderStatus.PARTIALLY_EXECUTED
    if order.filled_quantity >= order.quantity:
        order.status = models.OrderStatus.EXECUTED
    elif order.filled_quantity > 0:
        order.status = models.OrderStatus.PARTIALLY_EXECUTED
    elif order.order_type == models.OrderType.MARKET:
        # Рыночный ордер отменяется, если не был исполнен
        order.status = models.OrderStatus.CANCELLED
    
    order.updated_at = datetime.datetime.utcnow()
    db.commit()

def execute_deal(db: Session, order: models.Order, counter_order: models.Order, quantity: Decimal, price: Decimal):
    """
    Выполняет сделку между двумя ордерами.
    """
    # Определяем кто покупатель, а кто продавец
    if order.side == models.OrderSide.BUY:
        buyer_id = order.user_id
        seller_id = counter_order.user_id
        buyer_order = order
        seller_order = counter_order
    else:
        buyer_id = counter_order.user_id
        seller_id = order.user_id
        buyer_order = counter_order
        seller_order = order
    
    # Рассчитываем сумму сделки
    deal_amount = quantity * price
    
    # Обновляем балансы
    # 1. Покупатель получает актив
    buyer_asset_balance = db.query(models.Balance).filter(
        models.Balance.user_id == buyer_id,
        models.Balance.ticker == order.ticker
    ).first()
    
    if buyer_asset_balance:
        buyer_asset_balance.amount += quantity
    else:
        buyer_asset_balance = models.Balance(
            user_id=buyer_id,
            ticker=order.ticker,
            amount=quantity
        )
        db.add(buyer_asset_balance)
    
    # 2. Продавец получает рубли
    seller_rub_balance = db.query(models.Balance).filter(
        models.Balance.user_id == seller_id,
        models.Balance.ticker == "RUB"
    ).first()
    
    if seller_rub_balance:
        seller_rub_balance.amount += deal_amount
    else:
        seller_rub_balance = models.Balance(
            user_id=seller_id,
            ticker="RUB",
            amount=deal_amount
        )
        db.add(seller_rub_balance)
    
    # 3. Если у покупателя был лимитный ордер, оставшаяся часть зарезервированных средств возвращается
    if buyer_order.order_type == models.OrderType.LIMIT:
        reserved_amount = quantity * buyer_order.price
        refund_amount = reserved_amount - deal_amount
        
        if refund_amount > 0:
            buyer_rub_balance = db.query(models.Balance).filter(
                models.Balance.user_id == buyer_id,
                models.Balance.ticker == "RUB"
            ).first()
            
            if buyer_rub_balance:
                buyer_rub_balance.amount += refund_amount
    
    # Обновляем ордера
    order.filled_quantity += quantity
    counter_order.filled_quantity += quantity
    
    if counter_order.filled_quantity >= counter_order.quantity:
        counter_order.status = models.OrderStatus.EXECUTED
    else:
        counter_order.status = models.OrderStatus.PARTIALLY_EXECUTED
    
    counter_order.updated_at = datetime.datetime.utcnow()
    order.updated_at = datetime.datetime.utcnow()
    
    # Создаем запись о сделке
    # (в следующем этапе будет реализована таблица transactions)
    
    db.commit()

def cancel_order_and_return_funds(db: Session, order_id: str):
    """
    Отменяет ордер и возвращает зарезервированные средства.
    """
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        return
    
    # Проверяем, можно ли отменить ордер
    if order.status not in [models.OrderStatus.NEW, models.OrderStatus.PARTIALLY_EXECUTED]:
        return
    
    remaining_quantity = order.quantity - order.filled_quantity
    
    if remaining_quantity > 0:
        if order.side == models.OrderSide.BUY:
            # Возвращаем рубли только для лимитных ордеров
            if order.order_type == models.OrderType.LIMIT and order.price is not None:
                rub_balance = db.query(models.Balance).filter(
                    models.Balance.user_id == order.user_id,
                    models.Balance.ticker == "RUB"
                ).first()
                if rub_balance:
                    rub_balance.amount += remaining_quantity * order.price
                    
        elif order.side == models.OrderSide.SELL:
            # Возвращаем актив
            asset_balance = db.query(models.Balance).filter(
                models.Balance.user_id == order.user_id,
                models.Balance.ticker == order.ticker
            ).first()
            if asset_balance:
                asset_balance.amount += remaining_quantity
    
    # Устанавливаем статус в CANCELLED
    order.status = models.OrderStatus.CANCELLED
    order.updated_at = datetime.datetime.utcnow()
    db.commit()

def get_reserved_balance(db: Session, user_id: str, ticker: str) -> Decimal:
    """
    Подсчитывает сумму зарезервированного баланса в открытых ордерах.
    """
    reserved = Decimal(0)
    
    # Находим все открытые ордера пользователя для данного тикера
    open_orders = db.query(models.Order).filter(
        models.Order.user_id == user_id,
        models.Order.ticker == ticker,
        models.Order.status.in_([models.OrderStatus.NEW, models.OrderStatus.PARTIALLY_EXECUTED])
    ).all()
    
    for order in open_orders:
        if order.side == models.OrderSide.SELL:
            # Для ордеров на продажу резервируется количество актива
            reserved += order.quantity - order.filled_quantity
        elif order.side == models.OrderSide.BUY and order.order_type == models.OrderType.LIMIT:
            # Для лимитных ордеров на покупку резервируются рубли
            reserved += (order.quantity - order.filled_quantity) * order.price
            
    return reserved

def check_market_order_executable(
    db: Session,
    ticker: str,
    side: models.OrderSide,
    quantity: Decimal
) -> tuple[bool, str, Decimal]:
    """
    Проверяет возможность исполнения рыночного ордера.
    Возвращает (можно_исполнить, сообщение_об_ошибке, расчетная_стоимость)
    """
    counter_side = models.OrderSide.SELL if side == models.OrderSide.BUY else models.OrderSide.BUY
    price_order = asc if side == models.OrderSide.BUY else desc
    
    # Получаем все встречные ордера с положительным остатком
    counter_orders = db.query(models.Order).filter(
        models.Order.ticker == ticker,
        models.Order.side == counter_side,
        models.Order.status.in_([models.OrderStatus.NEW, models.OrderStatus.PARTIALLY_EXECUTED]),
        (models.Order.quantity - models.Order.filled_quantity) > 0  # Добавляем проверку на положительный остаток
    ).order_by(price_order(models.Order.price)).all()

    if not counter_orders:
        return False, f"Невозможно исполнить рыночный ордер - нет активных встречных заявок", Decimal(0)

    available_volume = sum(
        order.quantity - order.filled_quantity 
        for order in counter_orders
    )
    
    if available_volume < quantity:
        return False, (
            f"Невозможно исполнить рыночный ордер - недостаточно "
            f"{'предложений' if side == models.OrderSide.BUY else 'спроса'}. "
            f"Запрошено: {quantity}, доступно: {available_volume}"
        ), Decimal(0)

    # Рассчитываем примерную стоимость исполнения и проверяем ликвидность
    remaining = quantity
    total_cost = Decimal(0)
    matched_orders = 0
    
    for order in counter_orders:
        available = order.quantity - order.filled_quantity
        if available <= 0:
            continue
            
        matched = min(remaining, available)
        total_cost += matched * order.price
        remaining -= matched
        matched_orders += 1
        
        if remaining <= 0:
            break

    if matched_orders == 0:
        return False, "Недостаточно ликвидности для исполнения рыночного ордера", Decimal(0)

    return True, "", total_cost

def convert_db_status_to_api(status: models.OrderStatus) -> schemas.OrderStatus:
    """Конвертирует статус из БД в статус API"""
    status_map = {
        models.OrderStatus.NEW: schemas.OrderStatus.NEW,
        models.OrderStatus.PARTIALLY_EXECUTED: schemas.OrderStatus.PARTIALLY_EXECUTED,
        models.OrderStatus.EXECUTED: schemas.OrderStatus.EXECUTED,
        models.OrderStatus.CANCELLED: schemas.OrderStatus.CANCELLED
    }
    return status_map[status]

def convert_api_status_to_db(status: schemas.OrderStatus) -> models.OrderStatus:
    """Конвертирует статус из API в статус БД"""
    status_map = {
        schemas.OrderStatus.NEW: models.OrderStatus.NEW,
        schemas.OrderStatus.PARTIALLY_EXECUTED: models.OrderStatus.PARTIALLY_EXECUTED,
        schemas.OrderStatus.EXECUTED: models.OrderStatus.EXECUTED,
        schemas.OrderStatus.CANCELLED: models.OrderStatus.CANCELLED
    }
    return status_map[status]