"""Recompute the line numbers the JSON indexes store, from section numbers.

data/special_requirements_index.json, general_provisions_index.json and
personnel_index.json point into the markdown by line, so any edit to the text
(scripts/clean_text.py, a new ordinance) moves them. This finds each entry again
by what it is -- UDO §8.2, the "D." paragraph of §2.2, personnel II-1.04.01 --
and rewrites line_start. An entry it can't find is reported and fails the run.

    uv run python scripts/reindex_lines.py
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import corpus  # noqa: E402

DATA = ROOT / "data"


def udo_line(section: str) -> int | None:
    """Line of a UDO section heading, or of a lettered paragraph in one ("2.2D")."""
    m = re.fullmatch(r"(\d+\.\d+(?:\.\d+)*)([A-Z]?)", section)
    if not m:
        return None
    head = corpus.find("udo", m.group(1))
    if not head or not m.group(2):
        return head and head.line
    lines = (corpus.CORPORA["udo"]["dir"] / head.file).read_text().split("\n")
    for i in range(head.line, len(lines)):
        if i > head.line and lines[i].startswith("## "):
            break
        if re.match(rf"\s*{m.group(2)}\.\s", lines[i]):
            return i + 1
    return None


def main() -> int:
    missing = []
    for name, locate in [
        ("special_requirements_index.json", lambda e: udo_line(e["section"])),
        ("general_provisions_index.json", lambda e: udo_line(e["section"])),
        ("personnel_index.json",
         lambda e: (s := corpus.find("personnel", e["id"])) and s.file == e["filename"] and s.line),
    ]:
        path = DATA / name
        original = path.read_text()
        entries = json.loads(original)
        moved = 0
        for e in entries:
            line = locate(e)
            if not line:
                missing.append(f"{name}: {e.get('id') or e['section']}")
                continue
            moved += line != e["line_start"]
            e["line_start"] = line
        # keep each file's own escaping, so the diff is only line numbers
        path.write_text(json.dumps(entries, indent=2, ensure_ascii="\\u" in original) + "\n")
        print(f"  {name}: {len(entries)} entries, {moved} moved")
    for m in missing:
        print(f"  NOT FOUND {m}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
