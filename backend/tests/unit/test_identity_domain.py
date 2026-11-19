from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sapextract.core.exceptions import BusinessRuleError, PermissionDeniedError
from sapextract.identity.domain import totp
from sapextract.identity.domain.password_policy import PasswordPolicy, generate_temporary_password
from sapextract.identity.domain.permissions import (
    PERMISSION_CODES,
    PLATFORM_ADMIN,
    SYSTEM_ROLES,
)
from sapextract.identity.domain.rules import (
    ensure_admins_remain,
    ensure_can_grant,
    ensure_not_break_glass,
    ensure_not_self,
    lockout_until,
)

# ------------------------------------------------------------------ TOTP (RFC 6238, anexo B)
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


@pytest.mark.parametrize(
    ("unix_time", "expected"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_totp_rfc6238_vectors(unix_time: int, expected: str) -> None:
    assert totp.code_at(RFC_SECRET, totp.time_step(unix_time), digits=8) == expected


def test_totp_accepts_adjacent_step_and_rejects_replay() -> None:
    secret = totp.generate_secret()
    now = 1_800_000_000.0
    step = totp.time_step(now)
    previous = totp.code_at(secret, step - 1)
    assert totp.match_step(secret, previous, now, last_used_step=None) == step - 1
    # El mismo código (o uno anterior) ya usado no vuelve a valer.
    assert totp.match_step(secret, previous, now, last_used_step=step - 1) is None
    too_old = totp.code_at(secret, step - 3)
    assert totp.match_step(secret, too_old, now, last_used_step=None) is None
    assert totp.match_step(secret, "12ab56", now, last_used_step=None) is None


def test_provisioning_uri_contains_issuer_and_secret() -> None:
    uri = totp.provisioning_uri("ABCDEF", account="admin", issuer="SAPExtract")
    assert uri.startswith("otpauth://totp/SAPExtract%3Aadmin?")
    assert "secret=ABCDEF" in uri
    assert "issuer=SAPExtract" in uri


# ------------------------------------------------------------------ política de contraseñas
@pytest.mark.parametrize(
    ("password", "fragment"),
    [
        ("Corta1!", "al menos 12"),
        ("password1234", "conocida"),
        ("aaaaaaaaaaaaaa", "repetidos"),
        ("abcdefghijklmn", "secuencia"),
        ("Mi-jperez-2026!", "nombre de usuario"),
    ],
)
def test_password_policy_rejects(password: str, fragment: str) -> None:
    problems = PasswordPolicy(min_length=12).violations(password, username="jperez")
    assert any(fragment in p for p in problems), problems


def test_password_policy_accepts_strong_password() -> None:
    assert PasswordPolicy(min_length=12).violations("Rio-Verde.Tarde-42", username="ana") == []


def test_password_denylist_file(tmp_path: Path) -> None:
    path = tmp_path / "deny.txt"
    path.write_text("Empresa.Segura.2026\n", encoding="utf-8")
    policy = PasswordPolicy.build(min_length=12, denylist_file=path)
    assert policy.violations("empresa.segura.2026")


def test_generated_temporary_password_meets_policy() -> None:
    policy = PasswordPolicy(min_length=12)
    for _ in range(20):
        assert policy.violations(generate_temporary_password(), username="usuario") == []


# ------------------------------------------------------------------ reglas
def test_lockout_is_progressive_and_capped() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def until(attempts: int) -> datetime | None:
        return lockout_until(
            failed_attempts=attempts, max_attempts=5, base_minutes=5, max_minutes=60, now=now
        )

    assert until(4) is None
    assert until(5) == now + timedelta(minutes=5)
    assert until(6) == now + timedelta(minutes=10)
    assert until(20) == now + timedelta(minutes=60)


def test_cannot_grant_permissions_you_do_not_have() -> None:
    ensure_can_grant({"a", "b"}, {"a"})
    with pytest.raises(PermissionDeniedError) as exc:
        ensure_can_grant({"a"}, {"a", "c"})
    assert exc.value.error_code == "SX-IDN-ESCALATION"


def test_last_admin_and_self_rules() -> None:
    ensure_admins_remain(target_is_active_admin=False, other_active_admins=0)
    ensure_admins_remain(target_is_active_admin=True, other_active_admins=1)
    with pytest.raises(BusinessRuleError, match="último administrador"):
        ensure_admins_remain(target_is_active_admin=True, other_active_admins=0)
    with pytest.raises(BusinessRuleError):
        ensure_not_self(1, 1, "desactivar")
    with pytest.raises(BusinessRuleError):
        ensure_not_break_glass(True, "desactivar")


def test_system_roles_use_catalog_permissions() -> None:
    by_code = {r.code: r for r in SYSTEM_ROLES}
    assert by_code[PLATFORM_ADMIN].permissions == PERMISSION_CODES
    assert "pipeline:reset_delta" not in by_code["data_admin"].permissions
    assert by_code["auditor"].permissions >= {"audit:read", "audit:export"}
    assert all(r.permissions <= PERMISSION_CODES for r in SYSTEM_ROLES)
