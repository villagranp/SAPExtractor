"""Envío de avisos por canal (plan 09 §3): correo SMTP, Teams y webhook genérico firmado.

Las URL de los webhooks las escribe un usuario y las llama el servidor: cada conexión pasa por
la política de hosts permitidos (``SX_ALERT_ALLOWED_HOSTS``), como las conexiones SAP.
"""

from __future__ import annotations

import json
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlsplit

import httpx

from sapextract.core.config import Settings
from sapextract.core.exceptions import DomainError
from sapextract.core.http_guard import guarded_transport
from sapextract.core.network import HostPolicy
from sapextract.monitoring.domain.model import ChannelType, Message, sign

HTTP_TIMEOUT_SECONDS = 10.0
MAX_ERROR = 300


class ChannelError(Exception):
    """El canal no aceptó el aviso. El mensaje se guarda en ``notification.error``."""


@dataclass(frozen=True, kw_only=True)
class Delivery:
    """Lo que se envía: el mensaje legible y su versión estructurada (webhook genérico)."""

    message: Message
    payload: dict[str, Any]


type Sender = Callable[[ChannelType, dict[str, Any], dict[str, Any] | None, Delivery], None]


def check_url(url: str, policy: HostPolicy) -> str:
    """URL de webhook válida: HTTPS (salvo en local) y host permitido. Lanza ``ValueError``."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        msg = "La URL debe empezar por https:// e incluir el servidor."
        raise ValueError(msg)
    if parts.scheme == "http" and not policy.allow_loopback:
        msg = "La URL debe usar https://."
        raise ValueError(msg)
    policy.check_host(parts.hostname)
    return url.strip()


class ChannelSenders:
    """Implementación real de ``Sender``; las pruebas la sustituyen por un doble."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.policy = settings.alert_host_policy

    def __call__(
        self,
        kind: ChannelType,
        config: dict[str, Any],
        secret: dict[str, Any] | None,
        delivery: Delivery,
    ) -> None:
        match kind:
            case ChannelType.EMAIL:
                self._email(config, delivery.message)
            case ChannelType.TEAMS:
                url = str((secret or {}).get("url", ""))
                self._post(
                    url, {"text": f"**{delivery.message.title}**\n\n{delivery.message.text}"}
                )
            case ChannelType.WEBHOOK:
                body = json.dumps(delivery.payload, ensure_ascii=False).encode()
                hmac_secret = str((secret or {}).get("hmac_secret", ""))
                headers = {"X-SX-Signature": sign(hmac_secret, body)} if hmac_secret else {}
                self._post(str(config.get("url", "")), body, headers=headers)

    # -------------------------------------------------------------- correo
    def _email(self, config: dict[str, Any], message: Message) -> None:
        s = self.settings
        if not s.smtp_host or not s.smtp_from:
            msg = "El correo no está configurado en el servidor (SX_SMTP_HOST y SX_SMTP_FROM)."
            raise ChannelError(msg)
        recipients = [str(r) for r in config.get("recipients", [])]
        email = EmailMessage()
        email["Subject"] = message.title
        email["From"] = s.smtp_from
        email["To"] = ", ".join(recipients)
        email.set_content(message.text)
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=HTTP_TIMEOUT_SECONDS) as smtp:
                if s.smtp_starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if s.smtp_user and s.smtp_password:
                    smtp.login(s.smtp_user, s.smtp_password.get_secret_value())
                smtp.send_message(email)
        except (OSError, smtplib.SMTPException) as exc:
            msg = f"El servidor de correo rechazó el envío ({type(exc).__name__})."
            raise ChannelError(msg) from exc

    # -------------------------------------------------------------- webhooks
    def _post(
        self, url: str, body: dict[str, Any] | bytes, *, headers: dict[str, str] | None = None
    ) -> None:
        try:
            check_url(url, self.policy)
        except (ValueError, DomainError) as exc:
            raise ChannelError(getattr(exc, "detail", str(exc))) from exc
        transport = guarded_transport(self.policy, verify=ssl.create_default_context())
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        content = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        try:
            with httpx.Client(
                transport=transport, timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False
            ) as client:
                response = client.post(url, content=content, headers=request_headers)
        except (httpx.HTTPError, DomainError) as exc:
            msg = f"No se pudo conectar con el webhook ({type(exc).__name__})."
            raise ChannelError(msg) from exc
        if response.status_code >= 400:
            msg = f"El webhook respondió HTTP {response.status_code}."
            raise ChannelError(msg)
