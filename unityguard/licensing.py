"""Edition policy.

The first public release is a **complete free build**: every feature works, no
limits, no nag screens. Gating code stays here, switched off by one constant, so
a paid edition can be introduced later without touching the engine.

When `FREE_EDITION` is False the old behaviour returns: the free tier may run at
most `FREE_MAX_OPS` operations per plan, while audit / plan / rollback stay free.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .i18n import tr

KEY_PREFIX = "UG1"
SECRET = b"unityguard-v0.2-demo-secret-not-a-real-product-key"
LICENSE_FILE = "license.key"

TIER_FREE = "free"
TIER_PRO = "pro"

#: 完整免费版：所有功能开放，不限制单次操作数。
FREE_EDITION = True

FREE_MAX_OPS = 25

_LICENSE_DIRS = (".unityguard",)


@dataclass
class License:
    tier: str = TIER_FREE
    holder: str = ""
    source: str = ""
    valid: bool = True

    @property
    def is_pro(self) -> bool:
        return self.tier == TIER_PRO

    def label(self) -> str:
        if FREE_EDITION:
            return tr("免费完整版（全部功能）", "Free edition (all features)")
        if self.is_pro:
            return tr("Pro（{who}）", "Pro ({who})", who=self.holder or
                      tr("已授权", "licensed"))
        return tr("免费版", "Free tier")


def _sign(payload_b64: str) -> str:
    digest = hmac.new(SECRET, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return digest[:32]


def issue_key(tier: str = TIER_PRO, holder: str = "") -> str:
    payload = {"tier": tier, "holder": holder, "v": 1}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"{KEY_PREFIX}.{payload_b64}.{_sign(payload_b64)}"


def validate_key(key: str) -> License:
    parts = key.strip().split(".")
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        return License(tier=TIER_FREE, valid=False,
                       source=tr("授权码格式错误", "malformed licence key"))
    payload_b64, signature = parts[1], parts[2]
    if not hmac.compare_digest(_sign(payload_b64), signature):
        return License(tier=TIER_FREE, valid=False,
                       source=tr("授权码签名无效", "invalid licence signature"))
    try:
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return License(tier=TIER_FREE, valid=False,
                       source=tr("授权码内容损坏", "corrupted licence key"))
    tier = payload.get("tier", TIER_FREE)
    return License(tier=tier, holder=payload.get("holder", ""),
                   source=tr("授权码", "licence key"))


def _read_key_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def load_license(project_root: Path | None = None) -> License:
    env_key = os.environ.get("UNITYGUARD_LICENSE", "").strip()
    if env_key:
        return validate_key(env_key)

    candidates: list[Path] = []
    if project_root is not None:
        candidates.append(Path(project_root) / _LICENSE_DIRS[0] / LICENSE_FILE)
    home = Path.home()
    candidates.append(home / _LICENSE_DIRS[0] / LICENSE_FILE)

    for path in candidates:
        key = _read_key_file(path)
        if key:
            license_ = validate_key(key)
            license_.source = str(path)
            return license_
    return License(source=tr("内置（免费完整版）", "built-in (free edition)"))


def check_apply(plan, license_: License) -> list[str]:
    """Return human-readable reasons the plan cannot run on this edition."""
    if FREE_EDITION:
        return []
    reasons: list[str] = []
    # Temporary swap hops are bookkeeping, not extra work: do not charge for them.
    count = sum(1 for op in plan.operations if not getattr(op, "is_temp", False))
    if not license_.is_pro and count > FREE_MAX_OPS:
        reasons.append(tr(
            "免费版单次最多执行 {limit} 个操作，这个计划有 {count} 个。"
            "升级到 Pro 后可一次执行任意数量（回滚始终免费）。",
            "The free tier runs at most {limit} operations per plan; this one has {count}. "
            "Pro removes the cap (rollback is always free).",
            limit=FREE_MAX_OPS, count=count,
        ))
    return reasons


def save_license_key(key: str, *, project_root: Path | None = None,
                     home: bool = True) -> Path:
    base = Path.home() if home else Path(project_root or Path.cwd())
    directory = base / _LICENSE_DIRS[0]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LICENSE_FILE
    path.write_text(key.strip() + "\n", encoding="utf-8")
    return path
