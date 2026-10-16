# -*- coding: utf-8 -*-
"""Successor #2 credential-safety scan (v2).

Reads secret-like VALUES from the repo .env *in memory only* (values NEVER
printed), then scans evidence artifacts for those values and generic credential
shapes. Reports hits per key NAME + file + count so collisions can be judged.

Scope:
  A (strict; must be clean): successor-#2 run artifacts (0142-*).
  B (report): everything else in this evidence dir (predecessor artifacts,
    head-checkout repo copy) — listed only as findings, judged by key name.

Prints only key names / paths / counts. Excludes this script."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ENV_PATH = Path("D:/source_code/backtrader/.env")

SECRET_NAME_RE = re.compile(
    r"(PASSWORD|SECRET|TOKEN|API_KEY|APP_ID|AUTH_CODE|USER_ID|USER_PRODUCT_INFO|COOKIE)",
    re.IGNORECASE,
)

GENERIC_PATTERNS = [
    re.compile(r"(?i)\b(password|secret|token|api[_-]?key|auth[_-]?code)\b\s*[:=]\s*\S+"),
    re.compile(r"\b\d{4,8}@\d{2,4}#\S+"),  # CTP investor-id@broker#password shape
]

SKIP_NAMES = {"secret-scan.py", "secret-scan.txt",
              "secret-collision-analysis.py", "secret-collision-analysis.txt"}

STRICT_PREFIXES = ("0142-", "run-preflight-", "run-postrun-", "failures-identical-check")


def in_strict(p: Path) -> bool:
    """Strict scope = anything whose path is under a 0142-* dir / run snapshot."""
    rel = str(p.relative_to(HERE))
    return rel.split("\\")[0].split("/")[0].startswith(STRICT_PREFIXES)
# these predecessor artifacts contain pytest/console output and a repo copy
SCOPE_B_EXTRA_NOTE = "predecessor artifact"


def load_secret_values() -> list[tuple[str, str]]:
    """Return [(key, value)] pairs, in memory only."""
    pairs: list[tuple[str, str]] = []
    for raw in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if SECRET_NAME_RE.search(key) and len(value) >= 6:
            pairs.append((key, value))
    return pairs


def main() -> int:
    pairs = load_secret_values()
    print(f"env secret-like (key, value) pairs loaded: {len(pairs)} (values never printed)")
    for key, value in pairs:
        print(f"  key: {key}  value_len: {len(value)}  purely_numeric: {value.isdigit()}")

    files = [p for p in sorted(HERE.rglob("*")) if p.is_file() and p.name not in SKIP_NAMES]
    print(f"\nfiles to scan: {len(files)}")

    # per-(key,file) hits
    value_hits: dict[str, list[Path]] = {}
    generic_hits: list[tuple[Path, str]] = []
    strict_files = 0
    for path in files:
        try:
            text = path.read_bytes().decode("utf-8", errors="replace")
        except OSError as exc:
            print(f"  unreadable: {path}: {exc}")
            continue
        if in_strict(path):
            strict_files += 1
        for key, value in pairs:
            if value in text:
                value_hits.setdefault(key, []).append(path)
        for pat in GENERIC_PATTERNS:
            if pat.search(text):
                generic_hits.append((path, pat.pattern))
                break

    def rel(p: Path) -> str:
        return str(p.relative_to(HERE))

    strict_value_hits = []
    scope_b_value_hits = []
    for key, paths in sorted(value_hits.items()):
        for p in paths:
            entry = f"{key} -> {rel(p)}"
            if in_strict(p):
                strict_value_hits.append(entry)
            else:
                scope_b_value_hits.append(entry)

    print(f"\n=== SCOPE A: successor-#2 run artifacts (strict; must be clean) ===")
    print(f"strict artifact files scanned: {strict_files}")
    print(f"strict hits of real .env values: {len(strict_value_hits)}")
    for e in strict_value_hits:
        print(f"  REJECT {e}")

    print(f"\n=== SCOPE B: other evidence files (predecessor artifacts / head-checkout) ===")
    print(f"scope-B hits of real .env values (key -> file): {len(scope_b_value_hits)}")
    for e in scope_b_value_hits[:80]:
        print(f"  REVIEW {e}")
    if len(scope_b_value_hits) > 80:
        print(f"  ... and {len(scope_b_value_hits) - 80} more")

    print(f"\n=== generic credential-shape matches (all scopes) ===")
    seen_generic: set[tuple[str, str]] = set()
    for p, pat in generic_hits:
        key = (rel(p), pat)
        if key in seen_generic:
            continue
        seen_generic.add(key)
        if in_strict(p):
            print(f"  STRICT-SCOPE REVIEW {rel(p)} pattern={pat!r}")
    strict_generic = [g for g in generic_hits if in_strict(g[0])]
    print(f"generic matches in strict scope: {len(strict_generic)}")
    print(f"generic matches elsewhere (informational, fixture/example code): "
          f"{len({(rel(p), pat) for p, pat in generic_hits if not in_strict(p)})}")

    # Known, documented false positive: the replay report carries synthetic
    # nanosecond integer fields (e.g. 1_000_000_000_000_000_000) whose digit
    # runs embed the all-zeros public SimNow placeholder that equals the .env
    # CTP_AUTH_CODE value. Proven embedded-only (standalone=0), and the value
    # is the public all-same-char placeholder — see secret-collision-analysis.txt.
    fp = [h for h in strict_value_hits
          if h == "CTP_AUTH_CODE -> 0142-replay-evidence\\report.json"]
    unexplained = [h for h in strict_value_hits if h not in fp]
    print(f"\ndocumented false positives (all-zeros placeholder inside ns timestamps): {len(fp)}")
    for h in fp:
        print(f"  FP (resolved) {h}")
    ok = not unexplained and not strict_generic
    print(f"\nVERDICT (strict scope): "
          f"{'PASS — no unexplained credential value/pattern in successor-#2 artifacts' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
