"""Transporte ``httpx`` que aplica :class:`HostPolicy` en cada conexión TCP.

``httpcore`` abre las conexiones a través de un *network backend*: este resuelve el host,
valida todas sus IP y conecta con la primera ya validada. El SNI y la cabecera ``Host``
siguen usando el nombre original, así que TLS valida el certificado contra el nombre.
Con proxy, lo que se conecta y valida es el proxy; el host de destino se comprueba por
nombre antes de enviar (``HostPolicy.check_host``).
"""

from __future__ import annotations

import socket
import ssl
from collections.abc import Iterable

import httpcore
import httpx
from httpcore._backends.base import SOCKET_OPTION

from sapextract.core.network import HostPolicy


class GuardedNetworkBackend(httpcore.SyncBackend):
    def __init__(self, policy: HostPolicy) -> None:
        self.policy = policy

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        try:
            addresses = self.policy.resolve(host, port)
        except socket.gaierror as exc:
            raise httpcore.ConnectError(str(exc)) from exc
        return super().connect_tcp(addresses[0], port, timeout, local_address, socket_options)


def guarded_transport(
    policy: HostPolicy, *, verify: ssl.SSLContext, proxy: str | None = None
) -> httpx.HTTPTransport:
    transport = httpx.HTTPTransport(verify=verify, proxy=proxy, trust_env=False)
    pool = transport._pool  # httpx no expone el network backend
    if not hasattr(pool, "_network_backend"):  # pragma: no cover - cambio interno de httpcore
        msg = "La versión de httpcore no permite validar las conexiones salientes"
        raise RuntimeError(msg)
    pool._network_backend = GuardedNetworkBackend(policy)
    return transport
