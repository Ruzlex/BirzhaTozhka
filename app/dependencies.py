from fastapi import Depends, HTTPException, status, Header, Request
from sqlalchemy.orm import Session
from typing import Optional, Annotated
from .database import SessionLocal
from .models import User
from fastapi.security import APIKeyHeader
api_key_header = APIKeyHeader(name="Authorization", auto_error=True)

def get_db():
    """
    Зависимость для получения сессии базы данных.
    Автоматически закрывает сессию после использования.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(
    # Обязательный заголовок "Authorization" (alias="Authorization"), 
    # и отключаем замену пробелов подчёркиваниями (convert_underscores=False)
    authorization: Annotated[
        str, 
        Header(
            ..., 
            alias="Authorization", 
            convert_underscores=False, 
            description="API-ключ в формате: 'TOKEN your-api-key'"
        )
    ],
    db: Session = Depends(get_db)
) -> User:
    # Проверяем, что заголовок Authorization начинается с 'TOKEN '
    if not authorization.startswith("TOKEN "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный формат заголовка авторизации. Ожидается: 'TOKEN your-api-key'"
        )
    token = authorization[len("TOKEN "):]

    # Проверяем наличие пользователя с таким API-ключом
    user = db.query(User).filter(User.api_key == token).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или просроченный API-ключ"
        )
    return user


def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """
    Зависимость для получения текущего пользователя с правами администратора.
    Проверяет, что текущий пользователь имеет роль ADMIN.
    Используется для защиты административных эндпоинтов.
    """
    if current_user.role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ запрещен. Недостаточно прав. Требуется роль администратора."
        )
    return current_user

async def check_auth_headers(request: Request):
    """
    Вспомогательная функция для отладки заголовков авторизации.
    Показывает все заголовки запроса и проверяет наличие заголовка авторизации.
    """
    headers = dict(request.headers)
    auth_header = headers.get("Authorization") or headers.get("Authorization")
    
    return {
        "all_headers": headers,
        "auth_header": auth_header,
        "has_valid_header": auth_header is not None,
        "help": "Если has_valid_header=false, добавьте заголовок 'Authorization' с вашим API-ключом"
    }
