"""SAPExtract: orquestador de extracciones SAP (ODP/CDS vía OData)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sapextract")
except PackageNotFoundError:  # pragma: no cover - solo sin instalar
    __version__ = "0.0.0"
