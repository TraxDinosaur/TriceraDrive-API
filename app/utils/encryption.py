from __future__ import annotations

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import os

from app.config import get_settings


def _derive_key(secret: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100_000)
    return base64.urlsafe_b64encode(kdf.derive(secret.encode()))


def encrypt_value(plaintext: str) -> str:
    settings = get_settings()
    salt = os.urandom(16)
    key = _derive_key(settings.encryption_key, salt)
    f = Fernet(key)
    token = f.encrypt(plaintext.encode())
    return base64.urlsafe_b64encode(salt + token).decode()


def decrypt_value(ciphertext: str) -> str:
    settings = get_settings()
    raw = base64.urlsafe_b64decode(ciphertext.encode())
    salt, token = raw[:16], raw[16:]
    key = _derive_key(settings.encryption_key, salt)
    f = Fernet(key)
    return f.decrypt(token).decode()
