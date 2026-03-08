from cryptography.fernet import Fernet

from app.config import settings

_fernet = Fernet(settings.FERNET_KEY.encode())


def encrypt_token(token: str) -> str:
    """Encrypt a plaintext token (Google access/refresh) for DB storage."""
    return _fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt a stored token back to plaintext."""
    return _fernet.decrypt(encrypted.encode()).decode()
