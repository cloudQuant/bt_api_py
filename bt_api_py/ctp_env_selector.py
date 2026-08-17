"""CTP SimNow front selection helpers.

The public tuple-returning ``get_ctp_fronts`` API is kept for existing callers.
New code should prefer ``select_ctp_fronts`` so the selected environment and
reason can be displayed in gateway health and smoke reports.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

_FRONTS_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "ctp_fronts.yaml"

_HARDCODED_FRONTS = {
    "set1": {"td_front": "tcp://182.254.243.31:30001", "md_front": "tcp://182.254.243.31:30011"},
    "set2": {"td_front": "tcp://182.254.243.31:40001", "md_front": "tcp://182.254.243.31:40011"},
}


def _load_default_fronts() -> dict[str, dict[str, str]]:
    """加载默认前置地址（配置文件 → 硬编码兜底）。"""
    defaults = {k: dict(v) for k, v in _HARDCODED_FRONTS.items()}
    try:
        import yaml

        if _FRONTS_CONFIG_PATH.exists():
            with _FRONTS_CONFIG_PATH.open(encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                for key in ("set1", "set2"):
                    section = loaded.get(key)
                    if isinstance(section, dict):
                        for field in ("td_front", "md_front"):
                            if section.get(field):
                                defaults[key][field] = str(section[field])
    except Exception:  # noqa: BLE001 - 配置不可用时用硬编码兜底
        pass
    return defaults

_TRADING_SESSIONS = (
    (time(9, 0), time(11, 30)),
    (time(13, 30), time(15, 0)),
    (time(21, 0), time(23, 59, 59)),
)
_NIGHT_SESSION_AFTER_MIDNIGHT = (time(0, 0), time(2, 30))


@dataclass(frozen=True)
class CtpFrontSelection:
    """Resolved CTP front addresses and selection metadata."""

    td_front: str
    md_front: str
    env_name: str
    selection_reason: str
    selected_at: str
    requested_env: str
    set1_group: str = ""

    @property
    def selected_ctp_env(self) -> str:
        return self.env_name

    def to_dict(self) -> dict[str, str]:
        payload = asdict(self)
        payload["selected_ctp_env"] = self.env_name
        return payload


def _normalize_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(SHANGHAI_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=SHANGHAI_TZ)
    return now.astimezone(SHANGHAI_TZ)


def _is_weekday(value: datetime) -> bool:
    return value.weekday() < 5


def _in_trading_session(value: datetime) -> bool:
    current = value.time()
    if _NIGHT_SESSION_AFTER_MIDNIGHT[0] <= current <= _NIGHT_SESSION_AFTER_MIDNIGHT[1]:
        return True
    return any(start <= current <= end for start, end in _TRADING_SESSIONS)


def _is_set1_available(value: datetime) -> bool:
    current = value.time()
    if _NIGHT_SESSION_AFTER_MIDNIGHT[0] <= current <= _NIGHT_SESSION_AFTER_MIDNIGHT[1]:
        previous_weekday = (value.weekday() - 1) % 7
        return previous_weekday < 5
    return _is_weekday(value) and _in_trading_session(value)


def _set_env_fronts(td_front: str, md_front: str) -> None:
    os.environ["CTP_TD_FRONT"] = td_front
    os.environ["CTP_MD_FRONT"] = md_front


def _select_set1(
    *,
    requested_env: str,
    reason: str,
    selected_at: str,
    set1_group: str | None = None,
    apply_env: bool = True,
) -> CtpFrontSelection:
    group = str(set1_group or os.environ.get("CTP_SET1_GROUP", "1")).strip() or "1"
    fronts = _load_default_fronts()["set1"]
    td_front = os.environ.get(f"CTP_SET1_TD_FRONT_{group}", fronts["td_front"])
    md_front = os.environ.get(f"CTP_SET1_MD_FRONT_{group}", fronts["md_front"])
    if apply_env:
        _set_env_fronts(td_front, md_front)
    return CtpFrontSelection(
        td_front=td_front,
        md_front=md_front,
        env_name=f"set1_group{group}",
        selection_reason=reason,
        selected_at=selected_at,
        requested_env=requested_env,
        set1_group=group,
    )


def _select_set2(
    *,
    requested_env: str,
    reason: str,
    selected_at: str,
    apply_env: bool = True,
) -> CtpFrontSelection:
    fronts = _load_default_fronts()["set2"]
    td_front = os.environ.get("CTP_SET2_TD_FRONT", fronts["td_front"])
    md_front = os.environ.get("CTP_SET2_MD_FRONT", fronts["md_front"])
    if apply_env:
        _set_env_fronts(td_front, md_front)
    return CtpFrontSelection(
        td_front=td_front,
        md_front=md_front,
        env_name="set2_7x24",
        selection_reason=reason,
        selected_at=selected_at,
        requested_env=requested_env,
    )


def select_ctp_fronts(
    env: str = "",
    now: datetime | None = None,
    *,
    set1_group: str | None = None,
    apply_env: bool = True,
) -> CtpFrontSelection:
    """Resolve CTP TD/MD fronts for ``auto``, ``set1`` or ``set2``.

    ``auto`` uses Asia/Shanghai trading sessions. Legal holiday calendars are
    intentionally not consulted in this iteration; callers can force ``set1`` or
    ``set2`` when an exchange holiday makes the simple session rule inaccurate.
    """

    current = _normalize_now(now)
    requested_env = str(env or os.environ.get("CTP_ENV", "auto")).strip().lower() or "auto"
    selected_at = current.isoformat(timespec="seconds")

    if requested_env == "set1":
        return _select_set1(
            requested_env=requested_env,
            reason="forced_set1",
            selected_at=selected_at,
            set1_group=set1_group,
            apply_env=apply_env,
        )
    if requested_env == "set2":
        return _select_set2(
            requested_env=requested_env,
            reason="forced_set2",
            selected_at=selected_at,
            apply_env=apply_env,
        )
    if requested_env not in {"auto", ""}:
        return _select_set2(
            requested_env=requested_env,
            reason=f"unknown_env_fallback:{requested_env}",
            selected_at=selected_at,
            apply_env=apply_env,
        )
    if _is_set1_available(current):
        return _select_set1(
            requested_env="auto",
            reason="auto_regular_trading_session",
            selected_at=selected_at,
            set1_group=set1_group,
            apply_env=apply_env,
        )
    return _select_set2(
        requested_env="auto",
        reason="auto_outside_regular_session",
        selected_at=selected_at,
        apply_env=apply_env,
    )


def select_ctp_fronts_dict(
    env: str = "",
    now: datetime | None = None,
    *,
    set1_group: str | None = None,
    apply_env: bool = True,
) -> dict[str, str]:
    """Return ``select_ctp_fronts`` as a plain dict for JSON APIs."""

    return select_ctp_fronts(
        env=env,
        now=now,
        set1_group=set1_group,
        apply_env=apply_env,
    ).to_dict()


def get_ctp_fronts(env: str = "", now: datetime | None = None) -> tuple[str, str, str]:
    """Backward-compatible tuple API: ``(td_front, md_front, env_name)``."""

    selected = select_ctp_fronts(env=env, now=now)
    return selected.td_front, selected.md_front, selected.env_name


def apply_ctp_env() -> tuple[str, str, str]:
    """Apply selected fronts to ``CTP_TD_FRONT`` and ``CTP_MD_FRONT``."""

    return get_ctp_fronts()
