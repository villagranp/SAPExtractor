"""Certificados autofirmados para probar TLS y X.509 sin infraestructura externa."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


@dataclass(frozen=True)
class CertificatePair:
    cert_pem: str
    key_pem: str
    cert_path: Path
    key_path: Path


def make_certificate(
    directory: Path,
    *,
    common_name: str = "127.0.0.1",
    days: int = 365,
    passphrase: str | None = None,
    name: str = "server",
) -> CertificatePair:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    encryption: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(passphrase.encode())
        if passphrase
        else serialization.NoEncryption()
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    ).decode()
    directory.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = directory / f"{name}.crt", directory / f"{name}.key"
    cert_path.write_text(cert_pem, encoding="ascii")
    key_path.write_text(key_pem, encoding="ascii")
    return CertificatePair(cert_pem, key_pem, cert_path, key_path)
