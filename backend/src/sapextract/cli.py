"""CLI de administración: ``sapextract <comando>``."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from sapextract import __version__

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from sapextract.identity.application.toolkit import IdentityToolkit


@contextmanager
def _identity_session() -> Iterator[tuple[Session, IdentityToolkit]]:
    from sapextract.core.config import get_settings  # noqa: PLC0415
    from sapextract.core.db import Database  # noqa: PLC0415
    from sapextract.identity.application.toolkit import IdentityToolkit  # noqa: PLC0415

    settings = get_settings()
    db = Database(settings)
    try:
        with db.session() as session:
            yield session, IdentityToolkit.build(settings)
    finally:
        db.dispose()


def _print_error(exc: Exception) -> int:
    from sapextract.core.exceptions import DomainError  # noqa: PLC0415

    if isinstance(exc, DomainError):
        sys.stderr.write(f"Error: {exc.detail}\n")
        for issue in exc.errors:
            sys.stderr.write(f"  - {issue.message}\n")
        return 2
    raise exc


def _cmd_version(_: argparse.Namespace) -> int:
    sys.stdout.write(f"{__version__}\n")
    return 0


def _cmd_generate_master_key(_: argparse.Namespace) -> int:
    from sapextract.core.security.crypto import generate_master_key  # noqa: PLC0415

    sys.stdout.write(generate_master_key() + "\n")
    return 0


def _cmd_export_openapi(args: argparse.Namespace) -> int:
    from sapextract.core.config import Settings  # noqa: PLC0415
    from sapextract.main_api import create_app  # noqa: PLC0415

    # No necesita BD: el engine se crea de forma perezosa.
    settings = Settings(
        database_url="postgresql+psycopg://openapi@localhost/openapi", log_json=False
    )
    spec = create_app(settings).openapi()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": el mismo archivo en Windows y en CI (Linux), que lo compara byte a byte.
    out.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    sys.stdout.write(f"OpenAPI exportado en {out}\n")
    return 0


def _cmd_db_upgrade(args: argparse.Namespace) -> int:
    from sapextract.audit.application.maintenance import ensure_partitions  # noqa: PLC0415
    from sapextract.core.config import get_settings  # noqa: PLC0415
    from sapextract.core.exceptions import DomainError  # noqa: PLC0415
    from sapextract.core.migrations import upgrade  # noqa: PLC0415
    from sapextract.identity.application.bootstrap import (  # noqa: PLC0415
        ensure_bootstrap_admin,
        sync_catalog,
    )

    upgrade(get_settings().database_url, args.revision)
    if args.revision != "head":
        return 0
    with _identity_session() as (session, toolkit):
        ensure_partitions(session)
        sync_catalog(session)
        session.commit()
        sys.stdout.write("Catálogo de permisos y roles de sistema sincronizado.\n")
        try:
            created = ensure_bootstrap_admin(session, toolkit)
            session.commit()
        except DomainError as exc:
            sys.stderr.write("No se pudo crear el administrador inicial (SX_BOOTSTRAP_ADMIN_*).\n")
            return _print_error(exc)
    if created:
        sys.stdout.write(
            f"Administrador inicial «{created}» creado: deberá cambiar la contraseña al entrar.\n"
        )
    return 0


def _cmd_create_admin(args: argparse.Namespace) -> int:
    from sapextract.core.exceptions import DomainError  # noqa: PLC0415
    from sapextract.identity.application.bootstrap import (  # noqa: PLC0415
        create_local_admin,
        sync_catalog,
    )

    password: str | None = None
    if not args.generate:
        password = getpass.getpass("Contraseña inicial: ")
        if password != getpass.getpass("Repita la contraseña: "):
            sys.stderr.write("Error: las contraseñas no coinciden.\n")
            return 2
    try:
        with _identity_session() as (session, toolkit):
            sync_catalog(session)
            created = create_local_admin(
                session,
                toolkit,
                username=args.username,
                display_name=args.display_name or "",
                password=password,
            )
            session.commit()
    except DomainError as exc:
        return _print_error(exc)
    sys.stdout.write(f"Administrador «{created.username}» creado.\n")
    if created.generated:
        sys.stdout.write(f"Contraseña temporal (se muestra una sola vez): {created.password}\n")
    sys.stdout.write("Deberá cambiar la contraseña en el primer acceso.\n")
    return 0


def _cmd_reset_password(args: argparse.Namespace) -> int:
    from sapextract.core.exceptions import DomainError  # noqa: PLC0415
    from sapextract.identity.application.bootstrap import reset_password_cli  # noqa: PLC0415

    try:
        with _identity_session() as (session, toolkit):
            result = reset_password_cli(session, toolkit, username=args.username)
            session.commit()
    except DomainError as exc:
        return _print_error(exc)
    sys.stdout.write(f"Contraseña de «{result.username}» restablecida y cuenta desbloqueada.\n")
    sys.stdout.write(f"Contraseña temporal (se muestra una sola vez): {result.password}\n")
    return 0


def _cmd_audit_verify(_: argparse.Namespace) -> int:
    from sapextract.audit.domain.model import Actor  # noqa: PLC0415

    with _identity_session() as (session, toolkit):
        result = toolkit.audit.verify_and_record(session, actor=Actor.system("cli"))
    if result.ok:
        sys.stdout.write(f"Integridad correcta: {result.checked} eventos verificados.\n")
        return 0
    sys.stdout.write(
        f"CADENA ROTA en el evento seq={result.broken_seq} (id={result.broken_id}) "
        f"tras {result.checked} eventos correctos: {result.detail}\n"
    )
    return 1


def _cmd_db_current(_: argparse.Namespace) -> int:
    from sapextract.core.config import get_settings  # noqa: PLC0415
    from sapextract.core.db import Database  # noqa: PLC0415
    from sapextract.core.migrations import current_revisions, head_revisions  # noqa: PLC0415

    db = Database(get_settings())
    try:
        current, heads = current_revisions(db.engine), head_revisions()
    finally:
        db.dispose()
    sys.stdout.write(f"actual:   {', '.join(sorted(current)) or '(vacía)'}\n")
    sys.stdout.write(f"esperado: {', '.join(sorted(heads))}\n")
    return 0 if current == heads else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sapextract", description="Administración de SAPExtract")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="Muestra la versión").set_defaults(func=_cmd_version)
    sub.add_parser(
        "generate-master-key", help="Genera una clave maestra nueva para SX_MASTER_KEY"
    ).set_defaults(func=_cmd_generate_master_key)

    p = sub.add_parser("export-openapi", help="Exporta el contrato OpenAPI a un archivo")
    p.add_argument("--output", default="openapi.json")
    p.set_defaults(func=_cmd_export_openapi)

    db = sub.add_parser("db", help="Migraciones de la base de metadatos").add_subparsers(
        dest="db_command", required=True
    )
    up = db.add_parser("upgrade", help="Aplica migraciones")
    up.add_argument("revision", nargs="?", default="head")
    up.set_defaults(func=_cmd_db_upgrade)
    db.add_parser("current", help="Muestra la versión aplicada").set_defaults(func=_cmd_db_current)

    p = sub.add_parser(
        "create-admin", help="Crea un administrador local de emergencia (break-glass)"
    )
    p.add_argument("--username", required=True)
    p.add_argument("--display-name", default=None)
    p.add_argument(
        "--generate",
        action="store_true",
        help="Genera una contraseña temporal en lugar de pedirla",
    )
    p.set_defaults(func=_cmd_create_admin)

    p = sub.add_parser(
        "reset-password", help="Restablece la contraseña de un usuario local (recuperación)"
    )
    p.add_argument("--username", required=True)
    p.set_defaults(func=_cmd_reset_password)

    audit = sub.add_parser("audit", help="Auditoría").add_subparsers(
        dest="audit_command", required=True
    )
    audit.add_parser("verify", help="Verifica la cadena de hash").set_defaults(
        func=_cmd_audit_verify
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
