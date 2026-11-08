"""personal-kb-agent package."""

try:
    from importlib.metadata import version as _importlib_version
except ImportError:
    try:
        from importlib_metadata import version as _importlib_version  # type: ignore[import-not-found]
    except ImportError:
        _importlib_version = None  # type: ignore[assignment]

try:
    if _importlib_version is not None:
        __version__ = _importlib_version("personal-kb-agent")
    else:
        __version__ = "0.0.0+unknown"
except Exception as _exc:  # noqa: BLE001
    __version__ = "0.0.0+unknown"