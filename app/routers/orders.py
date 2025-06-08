from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc
from typing import List, Optional
from decimal import Decimal
from .. import models, schemas
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
    
    # Получаем заявки на покупку (по убыванию цены - самые высокие вверху)
    bids = db.query(models.Order).filter(
        models.Order.ticker == ticker,
        models.Order.side == models.OrderSide.BUY,
        models.Order.status == models.OrderStatus.OPEN
    ).order_by(desc(models.Order.price)).limit(limit).all()
    
    # Получаем заявки на продажу (по возрастанию цены - самые низкие вверху)
    asks = db.query(models.Order).filter(
        models.Order.ticker == ticker,
        models.Order.side == models.OrderSide.SELL,
        models.Order.status == models.OrderStatus.OPEN
    ).order_by(asc(models.Order.price)).limit(limit).all()
    
    # Агрегируем объемы по ценам
    bid_levels = {}
    for bid in bids:
        price = bid.price
        if price in bid_levels:
            bid_levels[price] += bid.quantity - bid.filled_quantity
        else:
            bid_levels[price] = bid.quantity - bid.filled_quantity

    ask_levels = {}
    for ask in asks:
        price = ask.price
        if price in ask_levels:
            ask_levels[price] += ask.quantity - ask.filled_quantity
        else:
            ask_levels[price] = ask.quantity - ask.filled_quantity

    # Формируем ответ
    result = {
        "bids": [{"price": price, "quantity": qty} for price, qty in sorted(bid_levels.items(), key=lambda x: x[0], reverse=True)],
        "asks": [{"price": price, "quantity": qty} for price, qty in sorted(ask_levels.items(), key=lambda x: x[0])]
    }
    
    return result

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
    order_type = schemas.OrderType.LIMIT if order.price is not None else schemas.OrderType.MARKET
    
    # Базовая валюта системы - RUB
    rub_instrument = db.query(models.Instrument).filter(models.Instrument.ticker == "RUB").first()
    if not rub_instrument:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Базовая валюта RUB не найдена в системе"
        )
    
    # Проверяем достаточность средств
    # При ПОКУПКЕ нужно проверить баланс рублей, при ПРОДАЖЕ - баланс инструмента
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
        # Для лимитного ордера нужно зарезервировать точную сумму
        if order_type == schemas.OrderType.LIMIT:
            required_amount = order.price * order.quantity
            if rub_balance.amount < required_amount:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Недостаточно средств. Требуется: {required_amount} RUB, доступно: {rub_balance.amount} RUB"
                )
        # Для рыночного ордера проверяем, что баланс просто положительный
        else:
            # Проверяем, что в стакане есть заявки на продажу
            sell_orders = db.query(models.Order).filter(
                models.Order.ticker == order.ticker,
                models.Order.side == models.OrderSide.SELL,
                models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED])
            ).order_by(asc(models.Order.price)).all()

            if not sell_orders:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Невозможно выполнить рыночную покупку — нет встречных ордеров"
                )

            # Проверяем, достаточно ли RUB хотя бы на 1 единицу по минимальной цене
            min_price = sell_orders[0].price
            if rub_balance.amount < min_price:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Недостаточно средств для рыночной покупки. Минимальная цена: {min_price} RUB, доступно: {rub_balance.amount}"
                )
    else:  # SELL
        # Находим баланс инструмента
        asset_balance = db.query(models.Balance).filter(
            models.Balance.user_id == current_user.id,
            models.Balance.ticker == order.ticker
        ).first()
        if not asset_balance or asset_balance.amount < order.quantity:
            available = asset_balance.amount if asset_balance else 0
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Недостаточно {order.ticker}. Требуется: {order.quantity}, доступно: {available}"
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
        status=models.OrderStatus.OPEN
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
    except Exception as e:
        # В случае ошибки при матчинге, отменяем ордер и возвращаем средства
        cancel_order_and_return_funds(db, new_order.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при выполнении матчинга: {str(e)}"
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
        if order.side == models.OrderSide.BUY and order.order_type == models.OrderType.LIMIT:
            # Возвращаем рубли
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
        if order.order_type == models.OrderType.LIMIT:
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
        if order.order_type == models.OrderType.LIMIT:
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

        if order.order_type == models.OrderType.MARKET and not counter_orders:
            raise ValueError(f"Нет встречных заявок для исполнения рыночного ордера {order.id}")
    
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
    if order.order_type == models.OrderType.MARKET and order_remaining > 0:
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