from cryptography.fernet import Fernet

from app.config import settings


# FERNET_KEY is validated in config.py at startup
_fernet = Fernet(settings.FERNET_KEY.get_secret_value().encode())


def encrypt_text(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_text(encrypted: str) -> str:
    return _fernet.decrypt(encrypted.encode()).decode()
