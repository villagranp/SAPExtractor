from __future__ import annotations

import pytest

from sapextract.core.security.crypto import (
    CryptoError,
    SecretCipher,
    decode_master_key,
    generate_master_key,
)


def _cipher(**keys: str) -> SecretCipher:
    current = next(iter(keys))
    return SecretCipher({k: decode_master_key(v) for k, v in keys.items()}, current)


def test_roundtrip_with_aad() -> None:
    cipher = _cipher(k1=generate_master_key())
    token = cipher.encrypt(b"clave-sap", aad=b"secret:123")
    assert "clave-sap" not in token
    assert cipher.decrypt(token, aad=b"secret:123") == b"clave-sap"


def test_same_plaintext_produces_different_tokens() -> None:
    cipher = _cipher(k1=generate_master_key())
    assert cipher.encrypt(b"x") != cipher.encrypt(b"x")


def test_wrong_aad_is_rejected() -> None:
    cipher = _cipher(k1=generate_master_key())
    token = cipher.encrypt(b"dato", aad=b"secret:1")
    with pytest.raises(CryptoError):
        cipher.decrypt(token, aad=b"secret:2")


def test_tampered_token_is_rejected() -> None:
    cipher = _cipher(k1=generate_master_key())
    prefix, key_id, wrapped, payload = cipher.encrypt(b"dato").split(".")
    tampered_payload = payload[:-2] + ("A" if payload[-2] != "A" else "B") + payload[-1]
    with pytest.raises(CryptoError):
        cipher.decrypt(".".join((prefix, key_id, wrapped, tampered_payload)))


def test_rotation_rewraps_with_current_key() -> None:
    old_key, new_key = generate_master_key(), generate_master_key()
    old = _cipher(k1=old_key)
    token = old.encrypt(b"dato", aad=b"ctx")

    rotated_cipher = SecretCipher(
        {"k2": decode_master_key(new_key), "k1": decode_master_key(old_key)}, "k2"
    )
    assert rotated_cipher.needs_rotation(token)
    rotated = rotated_cipher.rotate(token)
    assert not rotated_cipher.needs_rotation(rotated)
    assert rotated.split(".")[1] == "k2"

    only_new = _cipher(k2=new_key)
    assert only_new.decrypt(rotated, aad=b"ctx") == b"dato"


def test_unknown_key_id_is_rejected() -> None:
    token = _cipher(k1=generate_master_key()).encrypt(b"dato")
    with pytest.raises(CryptoError, match="no disponible"):
        _cipher(k9=generate_master_key()).decrypt(token)


@pytest.mark.parametrize("bad", ["", "no-base64-!!", "c2hvcnQ"])
def test_invalid_master_key(bad: str) -> None:
    with pytest.raises(CryptoError):
        decode_master_key(bad)
