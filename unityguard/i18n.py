"""Tiny bilingual layer (zh / en).

Every user-facing string goes through `tr(zh, en, **params)`. Keeping both
languages at the call site means a translation can never drift away from the
code that produces it - no key file to keep in sync, no missing entry that
silently renders as `report.title.3`.

Language resolution order: `--lang` flag (via set_language) > `UNITYGUARD_LANG`
environment variable > system locale (English systems get English, everything
else defaults to Chinese).
"""

from __future__ import annotations

import os

_LANG: str | None = None

#: Languages shipped with this build.
SUPPORTED = ("zh", "en")


def _normalize(lang: str | None) -> str | None:
    if not lang:
        return None
    lowered = lang.strip().lower().replace("-", "_")
    if lowered.startswith("zh"):
        return "zh"
    if lowered.startswith("en"):
        return "en"
    return None


def _system_language() -> str | None:
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        resolved = _normalize(os.environ.get(variable))
        if resolved:
            return resolved
    try:
        import locale

        code = (locale.getlocale()[0] or locale.getdefaultlocale()[0] or "")
    except (ValueError, AttributeError, TypeError):
        code = ""
    return _normalize(code)


def resolve() -> str:
    """Language to use when the user did not ask for one."""
    explicit = _normalize(os.environ.get("UNITYGUARD_LANG"))
    if explicit:
        return explicit
    system = _system_language()
    if system:
        return system
    # Unknown locale (CI, sandboxes): Chinese, because that is the primary market.
    return "zh"


def get_language() -> str:
    global _LANG
    if _LANG is None:
        _LANG = resolve()
    return _LANG


def set_language(lang: str) -> str:
    global _LANG
    resolved = _normalize(lang)
    if resolved is None:
        raise ValueError(f"unsupported language: {lang!r} (expected zh or en)")
    _LANG = resolved
    return resolved


def is_english() -> bool:
    return get_language() == "en"


def tr(zh: str, en: str, **params) -> str:
    """Pick the string for the current language and fill `{placeholders}`."""
    text = en if is_english() else zh
    if not params:
        return text
    try:
        return text.format(**params)
    except (KeyError, IndexError):
        return text
