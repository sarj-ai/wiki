from cryptography.fernet import Fernet, MultiFernet

from app.config import parse_fernet_keys, settings


def _multifernet(raw: str) -> MultiFernet:
    return MultiFernet([Fernet(k.encode()) for k in parse_fernet_keys(raw)])


_token_fernet = _multifernet(settings.FERNET_KEY.get_secret_value())
_cache_fernet = _multifernet((settings.CACHE_FERNET_KEY or settings.FERNET_KEY).get_secret_value())


def cache_decoder_identity() -> MultiFernet:
    return _cache_fernet


def encrypt_text(plaintext: str) -> str:
    return _token_fernet.encrypt(plaintext.encode()).decode()


def decrypt_text(encrypted: str) -> str:
    return _token_fernet.decrypt(encrypted.encode()).decode()


def encrypt_cache(plaintext: str) -> str:
    return encrypt_cache_bytes(plaintext.encode())


def decrypt_cache(encrypted: str, *, decoder: MultiFernet | None = None) -> str:
    return decrypt_cache_bytes(encrypted, decoder=decoder).decode()


def encrypt_cache_bytes(plaintext: bytes) -> str:
    return _cache_fernet.encrypt(plaintext).decode()


def decrypt_cache_bytes(encrypted: str, *, decoder: MultiFernet | None = None) -> bytes:
    return (decoder or _cache_fernet).decrypt(encrypted.encode())
