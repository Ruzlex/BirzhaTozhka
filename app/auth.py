import uuid
import secrets
import string
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str) -> str:
    """
    Создает хеш пароля с использованием bcrypt.
    """
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Проверяет, соответствует ли обычный пароль хешированному паролю.
    """
    return pwd_context.verify(plain_password, hashed_password)

def generate_api_key() -> str:
    """
    Генерирует безопасный и удобный API-ключ в формате "prefix_random_chars".
    Формат: toy_xxxxxxxxxxxxxxxxxxxx (всего 27 символов)
    """
    # Создаем префикс для более легкой идентификации
    prefix = "toy"
    
    # Создаем случайную строку из 20 символов (буквы и цифры)
    alphabet = string.ascii_letters + string.digits
    random_part = ''.join(secrets.choice(alphabet) for _ in range(20))
    
    # Объединяем префикс и случайную часть
    return f"{prefix}_{random_part}"
