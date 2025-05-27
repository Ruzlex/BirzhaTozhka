from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List

from ..dependencies import get_db
from .. import models, schemas

router = APIRouter(
    prefix="/api/v1/public",
    tags=["public"],
)

@router.get(
    "/transactions/{ticker}",
    response_model=List[schemas.Transaction],
    operation_id="get_transaction_history_api_v1_public_transactions__ticker__get",
    summary="Get Transaction History",
    description="История сделок для данного тикера",
)
def get_transaction_history(
    ticker: str,
    limit: int = Query(10, le=100),
    db: Session = Depends(get_db),
):
    # Проверяем существование инструмента
    instrument = (
        db.query(models.Instrument)
          .filter(models.Instrument.ticker == ticker)
          .first()
    )
    if not instrument:
        raise HTTPException(status_code=404, detail="Instrument not found")

    # Получаем последние сделки
    txs = (
        db.query(models.Transaction)
          .filter(models.Transaction.instrument_id == instrument.id)
          .order_by(models.Transaction.timestamp.desc())
          .limit(limit)
          .all()
    )

    # Маппим в Pydantic-схему
    return [
        schemas.Transaction(
            ticker=instrument.ticker,
            amount=int(tx.quantity),
            price=int(tx.price),
            timestamp=tx.timestamp,
        )
        for tx in txs
    ]
