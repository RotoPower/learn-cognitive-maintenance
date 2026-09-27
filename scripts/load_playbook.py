"""Load the operator playbook (docs/playbook/<mode>.md) into D1 through the admin API.

One file per failure mode; each `## Heading` becomes a row in D1 `playbook`
(mode, section, body). The dashboard shows the `actions` section in the asset drawer;
the Part E assistant reads all of them through GET /playbook.

    uv run python scripts/load_playbook.py              # PLANT_API_URL / PLANT_ADMIN_TOKEN from .env
    uv run python scripts/load_playbook.py --dry-run    # parse and print, send nothing
    uv run python scripts/load_playbook.py --url http://127.0.0.1:8787
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from plant.cli import ApiError, Client, Transport, _load_dotenv, _urllib_transport
from plant.sim import FAULT_MODES

PLAYBOOK_DIR = Path("docs/playbook")
MODES = tuple(FAULT_MODES)  # one playbook per failure mode the simulator knows
# heading text (lower case, starts with) -> D1 section key
SECTIONS = {
    "symptoms": "symptoms",
    "confirming checks": "checks",
    "immediate actions": "actions",
    "spare parts": "spares",
    "typical lead time": "lead_time",
}


def parse(text: str) -> dict[str, str]:
    """`## Heading` blocks -> {section key: markdown body}. Unknown headings are an error."""
    out: dict[str, str] = {}
    key, lines = None, []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if key:
                out[key] = "\n".join(lines).strip()
            head = m.group(1).lower()
            key = next((v for k, v in SECTIONS.items() if head.startswith(k)), None)
            if key is None:
                raise ValueError(f"unknown playbook section '{m.group(1)}'; expected {', '.join(SECTIONS)}")
            lines = []
        elif key:
            lines.append(line)
    if key:
        out[key] = "\n".join(lines).strip()
    missing = [v for v in SECTIONS.values() if not out.get(v)]
    if missing:
        raise ValueError(f"missing or empty sections: {', '.join(missing)}")
    return out


def load_all(directory: Path = PLAYBOOK_DIR) -> dict[str, dict[str, str]]:
    books = {}
    for mode in MODES:
        path = directory / f"{mode}.md"
        try:
            books[mode] = parse(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise ValueError(f"{path.as_posix()}: {e}") from None
    return books


def upload(books: dict[str, dict[str, str]], client: Client) -> list[dict]:
    return [client.call("POST", "/admin/playbook", body={"mode": mode, "sections": sections}) for mode, sections in books.items()]


def main(argv: list[str] | None = None, transport: Transport = _urllib_transport, env: dict | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default=str(PLAYBOOK_DIR))
    p.add_argument("--url", default=None, help="plant API base url (env PLANT_API_URL)")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    books = load_all(Path(a.dir))
    if a.dry_run:
        print(json.dumps({m: {k: len(v) for k, v in s.items()} for m, s in books.items()}, indent=2))
        return 0
    if env is None:
        _load_dotenv()
        env = os.environ
    token = env.get("PLANT_ADMIN_TOKEN")
    if not token:
        print("load_playbook: PLANT_ADMIN_TOKEN is not set", file=sys.stderr)
        return 3
    client = Client(a.url or env.get("PLANT_API_URL", "http://127.0.0.1:8000"), token, transport)
    try:
        for r in upload(books, client):
            print(f"loaded {r['mode']}: {', '.join(r['sections'])}")
    except ApiError as e:
        print(f"load_playbook: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
