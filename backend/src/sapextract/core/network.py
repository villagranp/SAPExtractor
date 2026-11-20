"""Hosts a los que la plataforma puede conectarse en nombre de un usuario (plan 13, C-07 a).

Las URL de SAP, proxies, servidores de tokens (y, más adelante, destinos y *webhooks*) las
escribe un usuario. Sin control, la prueba de conexión serviría para explorar la red interna
(SSRF). Reglas:

* Siempre bloqueadas: enlace local / metadatos de la nube (``169.254.0.0/16``, ``fe80::/10``)
  y direcciones no enrutables (``0.0.0.0/8``, ``::``).
* *Loopback* (``127.0.0.0/8``, ``::1``) solo en desarrollo y pruebas.
* Si hay lista de permitidos, el host debe coincidir con una entrada DNS (sufijo) o todas sus
  IP resueltas deben caer en una entrada IP/CIDR. La lista es obligatoria en PRD.

La comprobación se hace con las IP **resueltas** justo antes de conectar, y la conexión se abre
contra esa IP ya validada (no se vuelve a resolver): un DNS que cambie de respuesta entre la
validación y la conexión no puede llevar a una dirección bloqueada.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import ClassVar

from sapextract.core.exceptions import DomainError

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

ALWAYS_BLOCKED: tuple[IPNetwork, ...] = (
    ipaddress.ip_network("169.254.0.0/16"),  # enlace local / metadatos (AWS, Azure, GCP)
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::/128"),
)
LOOPBACK: tuple[IPNetwork, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)
_HOSTNAME = re.compile(
    r"^(\*\.|\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"
)


class HostNotAllowedError(DomainError):
    code: ClassVar[str] = "SX-NET-HOST-NOT-ALLOWED"
    status_code: ClassVar[int] = 422
    title: ClassVar[str] = "Destino de red no permitido"


def _ip(value: str) -> IPAddress | None:
    try:
        address = ipaddress.ip_address(value.strip("[]").split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def parse_entry(entry: str) -> str | IPNetwork:
    """Una entrada de la lista: red/IP (``10.0.0.0/8``, ``10.1.2.3``) o nombre DNS."""
    value = entry.strip().lower()
    try:
        return ipaddress.ip_network(value, strict=False)
    except ValueError:
        pass
    if not _HOSTNAME.match(value):
        msg = f"Entrada de hosts permitidos no válida: «{entry}»"
        raise ValueError(msg)
    return value.removeprefix("*.").removeprefix(".")


@dataclass(frozen=True)
class HostPolicy:
    """Política inmutable construida desde la configuración (una por proceso)."""

    allowed: tuple[str, ...] = ()
    allow_loopback: bool = False
    label: str = "SX_SAP_ALLOWED_HOSTS"
    _names: tuple[str, ...] = field(init=False, repr=False)
    _networks: tuple[IPNetwork, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        parsed = [parse_entry(e) for e in self.allowed if e.strip()]
        object.__setattr__(self, "_names", tuple(p for p in parsed if isinstance(p, str)))
        object.__setattr__(self, "_networks", tuple(p for p in parsed if not isinstance(p, str)))

    @property
    def restricted(self) -> bool:
        return bool(self._names or self._networks)

    # -------------------------------------------------------------- comprobaciones
    def check_host(self, host: str) -> None:
        """Comprobación sin resolver DNS (al guardar o probar, para dar el error en el campo).

        Rechaza IP literales bloqueadas y nombres que no coinciden con ninguna entrada DNS
        cuando la lista solo tiene nombres (si hay redes, decide la IP resuelta al conectar).
        """
        address = _ip(host)
        if address is not None:
            self.check_address(host, address)
            return
        if self.restricted and not self._networks and not self._name_allowed(host):
            raise self._denied(host, "no figura en la lista de hosts permitidos")

    def check_address(self, host: str, address: IPAddress) -> None:
        if any(address in net for net in ALWAYS_BLOCKED if net.version == address.version):
            raise self._denied(
                host, f"resuelve a {address}, una dirección de enlace local o no enrutable"
            )
        is_loopback = any(address in net for net in LOOPBACK if net.version == address.version)
        if is_loopback and not self.allow_loopback:
            raise self._denied(
                host, f"resuelve a {address} (loopback), solo permitido en desarrollo"
            )
        if not self.restricted or self._name_allowed(host):
            return
        if not any(address in net for net in self._networks if net.version == address.version):
            raise self._denied(host, f"resuelve a {address}, fuera de la lista de hosts permitidos")

    def resolve(self, host: str, port: int) -> list[str]:
        """IP de ``host`` validadas, en el orden de ``getaddrinfo``.

        Se rechaza el host si **alguna** IP está bloqueada (un nombre que resuelve a una IP
        válida y a otra de metadatos no debe pasar). Propaga ``socket.gaierror``.
        """
        literal = _ip(host)
        if literal is not None:
            self.check_address(host, literal)
            return [str(literal)]
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = _unique(str(info[4][0]) for info in infos)
        for value in addresses:
            address = _ip(value)
            if address is None:  # pragma: no cover - getaddrinfo siempre devuelve IP
                raise self._denied(host, f"devolvió una dirección no reconocida ({value})")
            self.check_address(host, address)
        return addresses

    # -------------------------------------------------------------- internos
    def _name_allowed(self, host: str) -> bool:
        name = host.strip().lower().rstrip(".")
        return any(name == entry or name.endswith(f".{entry}") for entry in self._names)

    def _denied(self, host: str, reason: str) -> HostNotAllowedError:
        msg = f"No se permite conectar con «{host}»: {reason}."
        if self.restricted:
            msg += f" Revise {self.label}."
        return HostNotAllowedError(msg)


def _unique(values: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value)
    return list(seen)
