"""Тесты CAPTCHA: генерация, шум, проверка ответа."""

from __future__ import annotations

from app.security.captcha import ALPHABET, generate_captcha
from app.security.crypto import hash_captcha, verify_captcha


def test_generates_four_symbols():
    captcha = generate_captcha(length=4)
    assert len(captcha.code) == 4
    assert all(char in ALPHABET for char in captcha.code)
    assert captcha.image.startswith(b"\x89PNG")


def test_hash_matches_code():
    captcha = generate_captcha(length=4)
    assert captcha.code_hash == hash_captcha(captcha.code)
    assert verify_captcha(captcha.code, captcha.code_hash)
    assert verify_captcha(captcha.code.lower(), captcha.code_hash)


def test_wrong_code_rejected():
    captcha = generate_captcha(length=4)
    wrong = "".join("A" if char != "A" else "B" for char in captcha.code)
    assert not verify_captcha(wrong, captcha.code_hash)


def test_code_not_plaintext_in_hash():
    captcha = generate_captcha(length=4)
    assert captcha.code not in captcha.code_hash
    assert len(captcha.code_hash) == 64


def test_images_are_unique():
    first = generate_captcha(length=4)
    second = generate_captcha(length=4)
    assert first.image != second.image
    assert first.code != second.code or first.code_hash != second.code_hash


def test_custom_length():
    captcha = generate_captcha(length=6)
    assert len(captcha.code) == 6
