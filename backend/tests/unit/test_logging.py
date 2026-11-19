from __future__ import annotations

from sapextract.core.logging import mask_secrets
from sapextract.core.masking import MASK


def test_masks_sensitive_keys_recursively() -> None:
    event = {
        "event": "sap_connection_saved",
        "password": "p@ss",
        "config": {"user": "RFC_USER", "client_secret": "abc", "nested": [{"api_key": "k"}]},
        "Authorization": "Basic xyz",
        "rows": 10,
    }
    masked = mask_secrets(None, "info", event)
    assert masked["password"] == MASK
    assert masked["Authorization"] == MASK
    assert masked["config"]["client_secret"] == MASK
    assert masked["config"]["nested"][0]["api_key"] == MASK
    assert masked["config"]["user"] == "RFC_USER"
    assert masked["rows"] == 10


def test_masks_credentials_inside_urls() -> None:
    masked = mask_secrets(
        None, "info", {"event": "x", "url": "postgresql+psycopg://app:supersecreta@db:5432/sx"}
    )
    assert "supersecreta" not in masked["url"]
    assert masked["url"] == f"postgresql+psycopg://app:{MASK}@db:5432/sx"
