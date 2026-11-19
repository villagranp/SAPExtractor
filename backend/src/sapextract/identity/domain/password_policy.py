"""Política de contraseñas locales (plan 02 §3)."""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass, field
from pathlib import Path

# Lista mínima incluida de contraseñas y patrones muy comunes. Para una lista amplia
# (p. ej. las 100 000 más filtradas) configure SX_PASSWORD_DENYLIST_FILE.
_BUILTIN_DENYLIST = frozenset(
    {
        "123456789012",
        "1234567890123",
        "12345678901234",
        "qwertyuiopas",
        "qwerty123456",
        "password1234",
        "password12345",
        "password123456",
        "passw0rd1234",
        "contrasena123",
        "contraseña123",
        "contrasena1234",
        "contraseña1234",
        "administrador",
        "administrator",
        "admin1234567",
        "admin123456789",
        "welcome12345",
        "bienvenido123",
        "bienvenido1234",
        "iloveyou1234",
        "letmein12345",
        "changeme1234",
        "cambiame1234",
        "sapextract123",
        "sapextract1234",
        "abcdefghijkl",
        "abc123456789",
        "111111111111",
        "000000000000",
        "aaaaaaaaaaaa",
        "temporal1234",
    }
)

_SEQUENCES = ("0123456789", "abcdefghijklmnopqrstuvwxyz", "qwertyuiopasdfghjklzxcvbnm")


@dataclass(frozen=True, kw_only=True)
class PasswordPolicy:
    min_length: int = 12
    max_length: int = 128
    denylist: frozenset[str] = field(default=_BUILTIN_DENYLIST)

    @classmethod
    def build(cls, *, min_length: int, denylist_file: Path | None = None) -> PasswordPolicy:
        denylist = set(_BUILTIN_DENYLIST)
        if denylist_file is not None:
            with denylist_file.open(encoding="utf-8", errors="ignore") as fh:
                denylist.update(line.strip().lower() for line in fh if line.strip())
        return cls(min_length=min_length, denylist=frozenset(denylist))

    def violations(self, password: str, *, username: str | None = None) -> list[str]:
        """Lista de incumplimientos en lenguaje de usuario (vacía si es válida)."""
        problems: list[str] = []
        if len(password) < self.min_length:
            problems.append(f"Debe tener al menos {self.min_length} caracteres.")
        if len(password) > self.max_length:
            problems.append(f"No puede superar {self.max_length} caracteres.")
        lowered = password.lower()
        if lowered in self.denylist:
            problems.append("Es una contraseña conocida o filtrada; elija otra.")
        if len(set(password)) <= 3:
            problems.append("Tiene demasiados caracteres repetidos.")
        if any(lowered in seq or lowered in seq[::-1] for seq in _SEQUENCES):
            problems.append("Es una secuencia de teclado o alfabética.")
        if username and len(username) >= 3 and username.lower() in lowered:
            problems.append("No puede contener el nombre de usuario.")
        return problems


def generate_temporary_password(length: int = 20) -> str:
    """Contraseña aleatoria con mayúsculas, minúsculas, dígitos y símbolos."""
    alphabet = string.ascii_letters + string.digits + "-_.!@#%+="
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in candidate)
            and any(c.isupper() for c in candidate)
            and any(c.isdigit() for c in candidate)
            and any(not c.isalnum() for c in candidate)
        ):
            return candidate
