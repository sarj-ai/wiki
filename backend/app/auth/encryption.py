from cryptography.fernet import Fernet

from app.config import settings

# FERNET_KEY is validated in config.py at startup
_fernet = Fernet(settings.FERNET_KEY.encode())


def encrypt_token(token: str) -> str:
    return _fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    return _fernet.decrypt(encrypted.encode()).decode()
