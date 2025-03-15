from fastapi import FastAPI
from app.routers import users
from app.database import engine, Base

# Создаем таблицы в базе данных при запуске
Base.metadata.create_all(bind=engine)

app = FastAPI()

# Подключение роутера пользователей
app.include_router(users.router)