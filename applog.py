"""
applog.py — central logging for SmartCart.

One stdout logger ("smartcart") so everything lands in Streamlit Cloud's log
panel with consistent, greppable lines. While we're hunting the
grocery-pipeline bugs, the default level is verbose (INFO shows every stage
snapshot + each pack-size adjustment). Turn it down later by setting
SMARTCART_LOG_LEVEL=WARNING in the environment / Streamlit secrets.

Usage:
    from applog import get_logger, log_items
    log = get_logger(__name__)
    log.info("matched %r -> %r", item_name, product_name)
    log_items(log, "home.parsed", items)      # snapshot one pipeline stage

Grep tips (Streamlit Cloud logs):
    STAGE        — item-list snapshot at a pipeline boundary
    PACK         — a pack-size quantity adjustment fired
    MATCH        — a single item's match result
"""

import logging
import os
import sys

_ROOT = "smartcart"
_CONFIGURED = False


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger = logging.getLogger(_ROOT)
    level_name = os.environ.get("SMARTCART_LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    # Guard against duplicate handlers across Streamlit reruns / re-imports.
    if not any(getattr(h, "_smartcart", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        ))
        handler._smartcart = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.propagate = False
    _CONFIGURED = True


def get_logger(name: str = "") -> logging.Logger:
    """Return a child logger under the 'smartcart' namespace."""
    _configure()
    short = name.split(".")[-1] if name else _ROOT
    return logging.getLogger(f"{_ROOT}.{short}")


def fmt_item(it: dict) -> str:
    """Compact one-line representation of a grocery / match item dict."""
    if not isinstance(it, dict):
        return repr(it)
    name = it.get("item_name") or it.get("name") or "?"
    qty = it.get("quantity", it.get("amount", ""))
    unit = it.get("unit", "")
    cat = it.get("category", "")
    tail = ""
    mp = it.get("matched_product")
    if mp:
        pname = mp.get("description") or mp.get("name") or "?"
        tail = f" -> {pname!r}"
    elif it.get("status"):
        tail = f" [{it['status']}]"
    return (f"{name!r} qty={qty}"
            f"{(' ' + unit) if unit else ''}"
            f"{(' cat=' + cat) if cat else ''}{tail}")


def log_items(logger: logging.Logger, stage: str, items: list) -> None:
    """Snapshot a pipeline stage: count + one line per item (INFO)."""
    items = items or []
    logger.info("STAGE %s: %d item(s)", stage, len(items))
    for it in items:
        logger.info("  %s | %s", stage, fmt_item(it))
