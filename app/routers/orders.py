from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, func
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
    Возвращает списки активных заявок на покупку (bids) и продажу (asks),
    отсортированные по наиболее выгодной цене.
    """
    # Проверяем, что инструмент существует
    instrument = db.query(models.Instrument).filter(models.Instrument.ticker == ticker).first()
    if not instrument:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Инструмент с тикером {ticker} не найден"
        )
    
    # Получаем активные ордера на покупку с положительным остатком
    buy_orders = db.query(models.Order).filter(
        models.Order.ticker == ticker,
        models.Order.side == models.OrderSide.BUY,
        models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED]),
        (models.Order.quantity - models.Order.filled_quantity) > 0  # Добавляем проверку на положительный остаток
    ).all()

    # Получаем активные ордера на продажу с положительным остатком
    sell_orders = db.query(models.Order).filter(
        models.Order.ticker == ticker,
        models.Order.side == models.OrderSide.SELL,
        models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED]),
        (models.Order.quantity - models.Order.filled_quantity) > 0  # Добавляем проверку на положительный остаток
    ).all()

    # Агрегируем объемы по ценам для покупок
    bid_levels = {}
    for order in buy_orders:
        remaining_qty = order.quantity - order.filled_quantity
        if remaining_qty <= 0:
            continue
        if order.price not in bid_levels:
            bid_levels[order.price] = Decimal('0')
        bid_levels[order.price] += remaining_qty

    # Агрегируем объемы по ценам для продаж
    ask_levels = {}
    for order in sell_orders:
        remaining_qty = order.quantity - order.filled_quantity
        if remaining_qty <= 0:
            continue
        if order.price not in ask_levels:
            ask_levels[order.price] = Decimal('0')
        ask_levels[order.price] += remaining_qty

    # Сортируем уровни и применяем лимит
    sorted_bids = sorted(
        [
            schemas.Level(price=price, qty=qty)
            for price, qty in bid_levels.items()
            if qty > 0  # Дополнительная проверка на положительный объем
        ],
        key=lambda x: (-x.price, -x.qty)  # Сортируем по убыванию цены, при равных ценах - по убыванию объема
    )[:limit]

    sorted_asks = sorted(
        [
            schemas.Level(price=price, qty=qty)
            for price, qty in ask_levels.items()
            if qty > 0  # Дополнительная проверка на положительный объем
        ],
        key=lambda x: (x.price, -x.qty)  # Сортируем по возрастанию цены, при равных ценах - по убыванию объема
    )[:limit]

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

    # Проверяем баланс и резервирование
    if order.side == schemas.OrderSide.SELL:
        # Проверяем баланс актива для продажи
        balance = db.query(models.Balance).filter(
            models.Balance.user_id == current_user.id,
            models.Balance.ticker == order.ticker
        ).first()

        if not balance:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"У вас нет баланса в {order.ticker}"
            )

        # Получаем сумму в активных ордерах
        reserved = db.query(models.Order).filter(
            models.Order.user_id == current_user.id,
            models.Order.ticker == order.ticker,
            models.Order.side == schemas.OrderSide.SELL,
            models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED])
        ).with_entities(
            func.sum(models.Order.quantity - models.Order.filled_quantity)
        ).scalar() or Decimal('0')

        available = balance.amount - reserved

        if available < order.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Недостаточно {order.ticker} для создания ордера. "
                    f"Всего: {balance.amount}, "
                    f"Зарезервировано: {reserved}, "
                    f"Доступно: {available}, "
                    f"Требуется: {order.quantity}"
                )
            )
    else:  # BUY
        # Проверяем баланс RUB для покупки
        balance = db.query(models.Balance).filter(
            models.Balance.user_id == current_user.id,
            models.Balance.ticker == "RUB"
        ).first()

        if not balance:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="У вас нет баланса в RUB"
            )

        if order_type == schemas.OrderType.LIMIT:
            # Для лимитного ордера проверяем точную сумму
            required = order.quantity * order.price

            # Получаем сумму в активных ордерах на покупку
            reserved = db.query(models.Order).filter(
                models.Order.user_id == current_user.id,
                models.Order.side == schemas.OrderSide.BUY,
                models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED])
            ).with_entities(
                func.sum((models.Order.quantity - models.Order.filled_quantity) * models.Order.price)
            ).scalar() or Decimal('0')

            available = balance.amount - reserved

            if available < required:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Недостаточно RUB для создания ордера. "
                        f"Всего: {balance.amount}, "
                        f"Зарезервировано: {reserved}, "
                        f"Доступно: {available}, "
                        f"Требуется: {required}"
                    )
                )
        else:  # MARKET
            # Проверяем наличие встречных ордеров
            sell_orders = db.query(models.Order).filter(
                models.Order.ticker == order.ticker,
                models.Order.side == models.OrderSide.SELL,
                models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED]),
                (models.Order.quantity - models.Order.filled_quantity) > 0
            ).order_by(asc(models.Order.price)).all()

            if not sell_orders:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Нет активных ордеров на продажу"
                )

            # Считаем максимальную возможную стоимость
            estimated_cost = Decimal('0')
            remaining_quantity = order.quantity
            
            for sell_order in sell_orders:
                available_qty = sell_order.quantity - sell_order.filled_quantity
                matched_qty = min(remaining_quantity, available_qty)
                estimated_cost += matched_qty * sell_order.price
                remaining_quantity -= matched_qty
                
                if remaining_quantity <= 0:
                    break

            if remaining_quantity > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Недостаточно предложений для покупки {order.quantity} {order.ticker}"
                )

            # Получаем сумму в активных ордерах на покупку
            reserved = db.query(models.Order).filter(
                models.Order.user_id == current_user.id,
                models.Order.side == schemas.OrderSide.BUY,
                models.Order.order_type == models.OrderType.LIMIT,
                models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED])
            ).with_entities(
                func.sum((models.Order.quantity - models.Order.filled_quantity) * models.Order.price)
            ).scalar() or Decimal('0')

            available = balance.amount - reserved

            if available < estimated_cost:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Недостаточно RUB для создания рыночного ордера. "
                        f"Всего: {balance.amount}, "
                        f"Зарезервировано: {reserved}, "
                        f"Доступно: {available}, "
                        f"Примерная стоимость: {estimated_cost}"
                    )
                )

    # Если все проверки пройдены, создаем ордер
    new_order = models.Order(
        user_id=current_user.id,
        instrument_id=instrument.id,
        ticker=order.ticker,
        order_type=order_type,
        side=order.side,
        quantity=order.quantity,
        price=order.price,
        filled_quantity=0,
        status=models.OrderStatus.OPEN
    )
    
    db.add(new_order)
    db.commit()
    db.refresh(new_order)
    
    # После создания ордера пытаемся его исполнить
    try:
        execute_matching(db, new_order.id)
    except Exception as e:
        # В случае ошибки отменяем ордер
        cancel_order_and_return_funds(db, new_order.id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
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
    return orders

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
    
    return order

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
    
    if order.status != models.OrderStatus.OPEN and order.status != models.OrderStatus.PARTIALLY_FILLED:
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
    
    # Если ордер не открыт, нечего матчить
    if order.status != models.OrderStatus.OPEN:
        return
    
    # Для рыночных ордеров ищем любые встречные заявки
    # Для лимитных - только с подходящей ценой
    if order.side == models.OrderSide.BUY:
        # Ищем заявки на продажу
        if order.order_type == schemas.OrderType.LIMIT:
            # Для лимитного ордера на покупку подходят заявки на продажу с ценой <= цены ордера
            counter_orders = db.query(models.Order).filter(
                models.Order.ticker == order.ticker,
                models.Order.side == models.OrderSide.SELL,
                models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED]),
                models.Order.price <= order.price
            ).order_by(asc(models.Order.price)).all()
        else:
            # Для рыночного ордера на покупку подходят любые заявки на продажу
            counter_orders = db.query(models.Order).filter(
                models.Order.ticker == order.ticker,
                models.Order.side == models.OrderSide.SELL,
                models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED])
            ).order_by(asc(models.Order.price)).all()
            if not counter_orders:
                raise ValueError(f"Нет встречных заявок для исполнения рыночного ордера {order.id}")
    else:
        # Ищем заявки на покупку
        if order.order_type == schemas.OrderType.LIMIT:
            # Для лимитного ордера на продажу подходят заявки на покупку с ценой >= цены ордера
            counter_orders = db.query(models.Order).filter(
                models.Order.ticker == order.ticker,
                models.Order.side == models.OrderSide.BUY,
                models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED]),
                models.Order.price >= order.price
            ).order_by(desc(models.Order.price)).all()
        else:
            # Для рыночного ордера на продажу подходят любые заявки на покупку
            counter_orders = db.query(models.Order).filter(
                models.Order.ticker == order.ticker,
                models.Order.side == models.OrderSide.BUY,
                models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED])
            ).order_by(desc(models.Order.price)).all()

        if order.order_type == schemas.OrderType.MARKET and not counter_orders:
            raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Нет встречных заявок для исполнения рыночного ордера {order.id}"
        )
    
    # Итеративно выполняем сделки
    for counter_order in counter_orders:
        # Если наш ордер уже полностью исполнен, выходим
        if order.filled_quantity >= order.quantity:
            break
        
        # Пропускаем собственные ордера
        if counter_order.user_id == order.user_id:
            continue
        
        # Определяем объем сделки
        order_remaining = order.quantity - order.filled_quantity
        counter_remaining = counter_order.quantity - counter_order.filled_quantity
        deal_quantity = min(order_remaining, counter_remaining)
        
        # Определяем цену сделки (берем цену ранее размещенного ордера)
        deal_price = counter_order.price
        
        # Выполняем сделку
        execute_deal(db, order, counter_order, deal_quantity, deal_price)
    
    # Проверяем, полностью ли исполнен ордер
    order_remaining = order.quantity - order.filled_quantity
    
    # Обновляем статус ордера
    if order_remaining == 0:
        order.status = models.OrderStatus.FILLED
    elif order.filled_quantity > 0:
        order.status = models.OrderStatus.PARTIALLY_FILLED
    
    # Для рыночных ордеров, если остался неисполненный объем, отменяем его
    if order.order_type == schemas.OrderType.MARKET and order_remaining > 0:
        order.status = models.OrderStatus.CANCELLED if order.filled_quantity == 0 else models.OrderStatus.PARTIALLY_FILLED
    
    if order.side == models.OrderSide.BUY and order.order_type == models.OrderType.LIMIT:
        refund_quantity = order.quantity - order.filled_quantity
        if refund_quantity > 0:
            refund_amount = refund_quantity * order.price
            rub_balance = db.query(models.Balance).filter(
                models.Balance.user_id == order.user_id,
                models.Balance.ticker == "RUB"
            ).first()
            if rub_balance:
                rub_balance.amount += refund_amount

    if order.side == models.OrderSide.SELL and order.order_type == models.OrderType.LIMIT:
        refund_quantity = order.quantity - order.filled_quantity
        if refund_quantity > 0:
            asset_balance = db.query(models.Balance).filter(
                models.Balance.user_id == order.user_id,
                models.Balance.ticker == order.ticker
            ).first()
            if asset_balance:
                asset_balance.amount += refund_quantity
    
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
        counter_order.status = models.OrderStatus.FILLED
    else:
        counter_order.status = models.OrderStatus.PARTIALLY_FILLED
    
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
    
    remaining_quantity = order.quantity - order.filled_quantity
    
    if remaining_quantity > 0:
        if order.side == models.OrderSide.BUY and order.order_type == models.OrderType.LIMIT:
            # Возвращаем рубли
            rub_balance = db.query(models.Balance).filter(
                models.Balance.user_id == order.user_id,
                models.Balance.ticker == "RUB"
            ).first()
            if not rub_balance:
                rub_balance = models.Balance(
                    user_id=order.user_id,
                    ticker="RUB",
                    amount=0
                )
                db.add(rub_balance)
                db.flush()
            rub_balance.amount += remaining_quantity * order.price
        elif order.side == models.OrderSide.SELL:
            # Возвращаем актив
            asset_balance = db.query(models.Balance).filter(
                models.Balance.user_id == order.user_id,
                models.Balance.ticker == order.ticker
            ).first()
            if not asset_balance:
                asset_balance = models.Balance(
                    user_id=order.user_id,
                    ticker=order.ticker,
                    amount=0
                )
                db.add(asset_balance)
                db.flush()
            asset_balance.amount += remaining_quantity
    
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
        models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED])
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
        models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED]),
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