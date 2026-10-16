# -*- coding: utf-8 -*-
"""Determine whether .env-value matches in scan hits are REAL leaks or numeric
collisions inside larger literals / public SimNow placeholders.

Prints only booleans, counts, and non-value context. Values never printed."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ENV = Path("D:/source_code/backtrader/.env")
E = Path("D:/temp/it10scn02-0142-livefix-20261016-r2")

PUBLIC_PLACEHOLDERS = {
    "simnow_client_test",
    "0000000000000000",
    "000000",
    "123456",
}


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def classify_occurrences(text: str, value: str) -> tuple[int, int]:
    """(embedded_in_longer_alnum_run, standalone)"""
    embedded = standalone = 0
    for m in re.finditer(re.escape(value), text):
        i, j = m.start(), m.end()
        prev = text[i - 1] if i > 0 else ""
        nxt = text[j] if j < len(text) else ""
        if prev.isalnum() or nxt.isalnum():
            embedded += 1
        else:
            standalone += 1
    return embedded, standalone


def main() -> int:
    env = load_env()
    keys = [
        "CTP_APP_ID",
        "CTP_AUTH_CODE",
        "CTP_USER_ID",
        "simnow_user_id",
        "HONGYUAN_PASSWORD",
        "HONGYUAN_USER_ID",
    ]
    print("key public-placeholder checks (value == known public const):")
    for k in keys:
        v = env.get(k, "")
        print(f"  {k}: is_known_public_placeholder={v in PUBLIC_PLACEHOLDERS}"
              f" all_same_char={len(set(v)) == 1 if v else None} len={len(v)}")

    appid = env.get("CTP_APP_ID", "")
    auth = env.get("CTP_AUTH_CODE", "")
    checks: list[tuple[str, str]] = [
        ("0142-replay-evidence/report.json", auth),
        ("head-checkout/examples/data/DOGEUSDT_15m_20200710_20260126.csv", env.get("CTP_USER_ID", "")),
        ("head-checkout/tests/datas/fake_kline_tokenusdt.csv", env.get("simnow_user_id", "")),
        ("head-checkout/examples/backtest/017_fnn_embedding/reports/reference/signals.jsonl",
         env.get("HONGYUAN_PASSWORD", "")),
    ]
    print("\noccurrence classification (embedded-in-longer-alnum-run vs standalone):")
    for rel, v in checks:
        p = E / rel
        if not v or not p.is_file():
            print(f"  {rel}: SKIP (no value or file)")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        emb, stand = classify_occurrences(text, v)
        print(f"  {rel}: total={emb + stand} embedded={emb} standalone={stand}")

    # sample embedded context for report.json occurrences (context chars only,
    # value itself redacted): show ~16 chars before match
    p = E / "0142-replay-evidence/report.json"
    if p.is_file() and auth:
        text = p.read_text(encoding="utf-8", errors="replace")
        occ = [m.start() for m in re.finditer(re.escape(auth), text)]
        print(f"\nreport.json auth-code occurrences: {len(occ)}")
        for i in occ[:6]:
            before = text[max(0, i - 16):i]
            after = text[i + len(auth):i + len(auth) + 4]
            print(f"  ctx_before=<{before}> ctx_after=<{after}> (match redacted, len={len(auth)})")

    # does the CTP_APP_ID value appear in the example support/source files,
    # and is it equal to its .env.example placeholder form there?
    p2 = E / "head-checkout/examples/unified/015_ctp_options_highfreq/.env.example"
    if p2.is_file() and appid:
        t2 = p2.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"CTP_APP_ID\s*=\s*(\S+)", t2)
        if m:
            print(f"\n.env.example CTP_APP_ID: equals_env_value={m.group(1).strip() == appid}")
    print("\ndone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
