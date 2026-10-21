"""Шифрование секретов пользователя (Fernet) и хеширование капчи."""

from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    digest = hashlib.sha256(settings.encryption_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    """Зашифровать строку (ключ API) для хранения в БД."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Расшифровать значение из БД. При повреждении вернёт пустую строку."""
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return ""


def hash_captcha(code: str) -> str:
    """Хеш ответа капчи: HMAC-SHA256 от кода и серверного секрета.

    Ответ капчи никогда не сохраняется в открытом виде и никогда не покидает
    сервер — ни в логи, ни в базу, ни в контекст языковой модели.
    """
    settings = get_settings()
    return hmac.new(
        settings.encryption_secret(), code.strip().upper().encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_captcha(code: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_captcha(code), expected_hash)
