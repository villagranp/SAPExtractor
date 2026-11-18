"""Registro de todos los modelos ORM (lo usa Alembic para ``--autogenerate``).

Cada módulo añade aquí la importación de sus modelos al implementarse.
"""

from sapextract.core.db import Base
from sapextract.core.system_settings import SystemSetting

__all__ = ["Base", "SystemSetting"]
