"""CLI de administración: ``sapextract <comando>``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from sapextract import __version__


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
    out.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sys.stdout.write(f"OpenAPI exportado en {out}\n")
    return 0


def _cmd_db_upgrade(args: argparse.Namespace) -> int:
    from sapextract.core.config import get_settings  # noqa: PLC0415
    from sapextract.core.migrations import upgrade  # noqa: PLC0415

    upgrade(get_settings().database_url, args.revision)
    return 0


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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
