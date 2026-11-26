"""The production image must carry the data the API serves, and content edits must be safe to publish."""

import re
import subprocess
import sys
from pathlib import Path

from app.services.packs import PACK_DIR, load_packs
from app.services.remote_config import DEFAULT_PATH, load_config

ROOT = Path(__file__).resolve().parents[1]


def _copied_paths() -> list[str]:
    out = []
    for line in (ROOT / "Dockerfile").read_text().splitlines():
        m = re.match(r"COPY\s+(?:--\S+\s+)*(\S+)\s+\S+\s*$", line.strip())
        if m:
            out.append(m.group(1).rstrip("/"))
    return out


def test_dockerfile_copies_every_runtime_data_dir():
    copied = _copied_paths()
    for needed in (
        PACK_DIR,
        DEFAULT_PATH.parent,
        ROOT / "data" / "cards" / "catalog.json",
        ROOT / "data" / "cards" / "intel.json",
    ):
        rel = needed.relative_to(ROOT).as_posix()
        assert any(rel == c or rel.startswith(c.rstrip("/") + "/") for c in copied), (
            f"Dockerfile does not COPY {rel}; the live API would 500 or serve nothing for it"
        )


def test_shipped_content_loads():
    assert {"tips", "walkthrough", "parser_rules"} <= load_packs(PACK_DIR).keys()
    assert load_config()["version"] > 0


def test_content_check_script_passes():
    r = subprocess.run(
        [sys.executable, "scripts/content.py", "check"], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr
