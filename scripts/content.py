"""Check and bump the server-delivered content: packs, remote config, card catalog.

Everything under data/packs, data/config and data/cards is shipped to phones by the API without an app
release (GET /v1/packs, /v1/config, /v1/catalog/cards). Phones apply a download only when its version is
newer than what they hold, so every content change must bump its version. This tool checks that.

    python scripts/content.py check                 # files are valid
    python scripts/content.py check --base origin/main   # ... and changed files bumped their version
    python scripts/content.py bump tips             # tips pack version + 1
    python scripts/content.py bump config           # remote config version = today (YYYYMMDD, or +1 if not newer)
    python scripts/content.py bump catalog          # catalog_version = today's date (or the day after the current one)
    python scripts/content.py pubkey                # public half of the signing key (needs CONFIG_SIGNING_KEY or FERNET_KEYS)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PACKS = ROOT / "data" / "packs"
CONFIG = ROOT / "data" / "config" / "remote_config.json"
CATALOG = ROOT / "data" / "cards" / "catalog.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _catalog_version(doc: dict) -> int:
    return int(str(doc["catalog_version"]).replace("-", ""))


def _version_of(rel: str, text: str) -> int | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if rel == "data/cards/catalog.json":
        return _catalog_version(data)
    v = data.get("version")
    return v if isinstance(v, int) else None


def validate() -> list[str]:
    errs: list[str] = []
    from app.services import packs as packs_mod
    from app.services import remote_config as rc
    from app.services.card_catalog import public_catalog

    try:
        packs_mod.load_packs.__wrapped__(PACKS)
    except Exception as e:  # noqa: BLE001 - report any bad pack
        errs.append(f"packs: {e}")
    try:
        rc.load_config(CONFIG)
    except Exception as e:  # noqa: BLE001
        errs.append(f"remote config: {e}")
    try:
        doc = _load(CATALOG)
        out = public_catalog(doc)
        if not out["cards"] or not isinstance(out["version"], int):
            errs.append("catalog: no cards or bad version")
    except Exception as e:  # noqa: BLE001
        errs.append(f"catalog: {e}")
    for f in sorted(PACKS.glob("parser_rules.json")):
        for i, r in enumerate(_load(f).get("sms", [])):
            if not (
                isinstance(r, dict) and {"institution", "pattern"} <= r.keys() and "{AMT}" in r["pattern"]
            ):
                errs.append(f"parser_rules sms[{i}]: needs institution and a pattern containing {{AMT}}")
            else:
                try:
                    re.compile(re.sub(r"\{(AMT|L4|M|DATE)\}", ".*", r["pattern"]))
                except re.error as e:
                    errs.append(f"parser_rules sms[{i}]: bad pattern ({e})")
    return errs


def changed_without_bump(base: str) -> list[str]:
    errs: list[str] = []
    files = [*sorted(PACKS.glob("*.json")), CONFIG, CATALOG]
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        old = subprocess.run(  # noqa: S603
            ["git", "show", f"{base}:{rel}"],  # noqa: S607
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if old.returncode != 0:
            continue  # new file
        new = f.read_text(encoding="utf-8")
        try:
            same = json.loads(old.stdout) == json.loads(new)
        except ValueError:
            same = old.stdout == new
        if same:
            continue
        ov, nv = _version_of(rel, old.stdout), _version_of(rel, new)
        if ov is not None and nv is not None and nv <= ov:
            errs.append(
                f"{rel} changed but its version is still {nv} (phones ignore it until it goes up): run `python scripts/content.py bump ...`"
            )
    return errs


def _sub_once(path: Path, pattern: str, repl: str) -> None:
    """Edit one value in place so the file's formatting (and the git diff) stays untouched."""
    text = path.read_text(encoding="utf-8")
    out, n = re.subn(pattern, repl, text, count=1)
    if n != 1:
        raise SystemExit(f"could not find the version field in {path.name}")
    path.write_text(out, encoding="utf-8")


def bump(what: str) -> str:
    today = int(datetime.now(UTC).strftime("%Y%m%d"))
    if what == "config":
        new = max(today, _load(CONFIG)["version"] + 1)
        _sub_once(CONFIG, r'("version"\s*:\s*)\d+', rf"\g<1>{new}")
        return f"remote config version {new}"
    if what == "catalog":
        cur = date(*map(int, str(_load(CATALOG)["catalog_version"]).split("-")))
        nxt = max(datetime.now(UTC).date(), cur + timedelta(days=1)).isoformat()
        _sub_once(CATALOG, r'("catalog_version"\s*:\s*")[0-9-]+', rf"\g<1>{nxt}")
        return f"catalog_version {nxt}"
    path = PACKS / f"{what}.json"
    if not path.exists():
        raise SystemExit(
            f"no pack named {what!r}; packs: {', '.join(p.stem for p in sorted(PACKS.glob('*.json')))}"
        )
    new = _load(path)["version"] + 1
    _sub_once(path, r'("version"\s*:\s*)\d+', rf"\g<1>{new}")
    return f"pack {what} version {new}"


def pubkey() -> str:
    from app.services.remote_config import derive_seed, public_key_b64

    seed = os.environ.get("CONFIG_SIGNING_KEY") or ""
    if not seed and os.environ.get("FERNET_KEYS"):
        seed = derive_seed(os.environ["FERNET_KEYS"].split(",")[0].strip())
    if not seed:
        raise SystemExit(
            "set CONFIG_SIGNING_KEY (or FERNET_KEYS) in the environment; the key is never printed"
        )
    return public_key_b64(seed)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--base", help="git ref to compare against, e.g. origin/main")
    b = sub.add_parser("bump")
    b.add_argument("what", help="a pack name, 'config' or 'catalog'")
    sub.add_parser("pubkey")
    a = ap.parse_args()
    if a.cmd == "check":
        errs = validate() + (changed_without_bump(a.base) if a.base else [])
        for e in errs:
            print("ERROR:", e)
        print("content ok" if not errs else f"{len(errs)} problem(s)")
        return 1 if errs else 0
    if a.cmd == "bump":
        print(bump(a.what))
    else:
        print(pubkey())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
