"""Cadena de migraciones: una sola cabeza y sin bifurcaciones (plan 13, C-22).

Con dos personas creando migraciones, dos ramas que parten de la misma revisión dejan dos
cabezas y ``sapextract db upgrade`` falla. Quien integra segundo hace *rebase* y apunta su
``down_revision`` a la última migración de ``main``.
"""

from __future__ import annotations

from alembic.script import ScriptDirectory

from sapextract.core.migrations import alembic_config, head_revisions


def test_single_alembic_head() -> None:
    heads = head_revisions()
    assert len(heads) == 1, (
        f"Hay {len(heads)} cabezas de Alembic ({', '.join(sorted(heads))}): haga rebase y "
        "apunte el down_revision de su migración a la última de main."
    )


def test_migration_chain_is_linear() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    revisions = list(script.walk_revisions())
    assert revisions, "No hay migraciones"
    for rev in revisions:
        assert not rev.is_branch_point, f"{rev.revision} tiene varias migraciones hijas"
        assert not rev.is_merge_point, f"{rev.revision} es una migración de merge"
    # Convención del repositorio: el identificador empieza por su número de orden.
    numbers = sorted(int(rev.revision.split("_", 1)[0]) for rev in revisions)
    assert numbers == list(range(1, len(revisions) + 1)), numbers
