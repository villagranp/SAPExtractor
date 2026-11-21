"""Tareas diarias de sistema (plan 03 §4, plan 08 §2): las ejecuta el scheduler líder.

Cada tarea guarda cuándo terminó por última vez en ``system_setting``: un reinicio del scheduler
no la repite antes de tiempo y una que falla se reintenta en la siguiente pasada.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog

from sapextract.core.clock import utcnow
from sapextract.core.db import Database
from sapextract.core.system_settings import SystemSetting

log = structlog.get_logger(__name__)

LAST_RUN_KEY = "maintenance.last_run"
DEFAULT_INTERVAL = timedelta(hours=24)


@dataclass(frozen=True)
class SystemTask:
    """``run`` devuelve un resumen sin datos sensibles: va al log técnico."""

    name: str
    run: Callable[[], Mapping[str, Any]]


class MaintenanceRunner:
    def __init__(
        self,
        db: Database,
        tasks: Sequence[SystemTask],
        *,
        interval: timedelta = DEFAULT_INTERVAL,
        clock: Callable[[], datetime] = utcnow,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> None:
        self.db = db
        self.tasks = tasks
        self.interval = interval
        self.clock = clock
        self.should_stop = should_stop

    def run_due(self) -> list[str]:
        """Ejecuta las tareas a las que les toca. Devuelve las que terminaron bien."""
        done: list[str] = []
        last = self._last_runs()
        for task in self.tasks:
            if self.should_stop():
                break
            previous = last.get(task.name)
            if previous is not None and self.clock() - previous < self.interval:
                continue
            try:
                summary = dict(task.run())
            except Exception:
                # Una tarea que falla no impide las demás; se reintenta en la siguiente pasada.
                log.exception("maintenance_task_failed", task=task.name)
                continue
            self._mark(task.name)
            log.info("maintenance_task_done", task=task.name, **summary)
            done.append(task.name)
        return done

    def _last_runs(self) -> dict[str, datetime]:
        with self.db.session() as session:
            setting = session.get(SystemSetting, LAST_RUN_KEY)
            raw = dict(setting.value) if setting is not None else {}
        result: dict[str, datetime] = {}
        for name, value in raw.items():
            try:
                result[name] = datetime.fromisoformat(str(value))
            except ValueError:
                continue
        return result

    def _mark(self, name: str) -> None:
        with self.db.session() as session:
            setting = session.get(SystemSetting, LAST_RUN_KEY)
            stamp = self.clock().isoformat()
            if setting is None:
                session.add(
                    SystemSetting(
                        key=LAST_RUN_KEY,
                        value={name: stamp},
                        description="Última ejecución correcta de cada tarea diaria de sistema",
                    )
                )
            else:
                setting.value = {**dict(setting.value), name: stamp}
            session.commit()
