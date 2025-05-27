from fastapi import APIRouter, Depends, HTTPException, status, Security
from sqlalchemy.orm import Session
from .. import schemas, models
from ..dependencies import get_db, get_current_user, get_current_admin
from decimal import Decimal
from typing import List

# Публичный роутер для получения списка инструментов
router = APIRouter(prefix="/api/v1/public", tags=["public"])

@router.get("/instrument", response_model=List[schemas.Instrument], summary="List Instruments", description="Список доступных инструментов")
def list_instruments(db: Session = Depends(get_db)):
    """
    Получить список всех доступных инструментов.
    """
    instruments = db.query(models.Instrument).all()
    result = []
    for instrument in instruments:
        result.append({
            "ticker": instrument.ticker,
            "name": instrument.name
        })
    return result


admin_router = APIRouter(
         prefix="/api/v1/admin", 
         tags=["admin"]
     )
@admin_router.post("/instrument", response_model=schemas.Ok, summary="Add Instrument")
def add_instrument(
    instrument: schemas.Instrument,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin)
):
    """
    Создать новый инструмент.
    
    Требуется авторизация с API-ключом пользователя, имеющего роль ADMIN,
    передаваемая в заголовке Authorization.
    """
    existing = db.query(models.Instrument).filter(models.Instrument.ticker == instrument.ticker).first()
    if existing:
        raise HTTPException(status_code=400, detail="Инструмент с таким тикером уже существует.")
    
    new_instrument = models.Instrument(
        ticker=instrument.ticker,
        name=instrument.name,
        instrument_type="stock",
        commission_rate=0.0,
        initial_price=0.0,
        available_quantity=0,
        is_listed=True
    )
    db.add(new_instrument)
    db.commit()
    
    return {"success": True}

@admin_router.delete("/instrument/{ticker}", response_model=schemas.Ok, summary="Delete Instrument", description="Удаление инструмента")
def delete_instrument(
    ticker: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin)
):
    """
    Удалить инструмент по его тикеру.
    
    Требуется авторизация с API-ключом пользователя, имеющего роль ADMIN.
    """
    instrument = db.query(models.Instrument).filter(models.Instrument.ticker == ticker).first()
    if not instrument:
        raise HTTPException(status_code=404, detail="Инструмент не найден.")

    # Удаляем связанные балансы
    db.query(models.Balance).filter(models.Balance.ticker == ticker).delete()
    # Удаляем связанные ордера
    db.query(models.Order).filter(models.Order.ticker == ticker).delete()

    db.delete(instrument)
    db.commit()
    return {"success": True}
