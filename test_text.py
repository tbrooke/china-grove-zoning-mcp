"""The ordinance text stays faithful to its sources.

Two independent readings of the Permitted Uses Table must agree: the markdown
table rebuilt from the PDF (scripts/rebuild_tables.py) and the curated
data/permitted_uses.json that lookup_permitted_use / can_i_build answer from.
A disagreement means one of them is wrong.
"""

import difflib
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
CODES = {"R-P": "R-P", "R-S": "R-S", "R-T": "R-T", "R-M": "R-M", "RMH": "R-MH", "R-MH": "R-MH",
         "N-C": "N-C", "O&I": "O-I", "O-I": "O-I", "C-B": "C-B", "H-B": "H-B", "C-P": "C-P",
         "L-I": "L-I", "H-I": "H-I", "PUD": "PUD"}

# The JSON names these by heading + sub-row; the table prints the heading
# ("General Construction Offices and Facilities") above the two sub-rows.
SUB_ROWS = {
    "General Construction Offices and Facilities With Outside Storage": "With Outside Storage",
    "General Construction Offices and Facilities Without Outside Storage": "Without Outside Storage",
}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def table_rows() -> dict[str, dict[str, str]]:
    md = (ROOT / "markdown" / "Chapter-07-Zoning-Districts-and-Permitted-Use-Table.md").read_text()
    rows, header = {}, None
    for line in md.split("\n"):
        if not line.startswith("|"):
            header = None
            continue
        if line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = cells
            continue
        districts = {CODES[h]: i for i, h in enumerate(header) if h in CODES}
        if len(districts) >= 10 and "Use" in header and cells[header.index("Use")]:
            rows[norm(cells[header.index("Use")])] = {
                d: (cells[i] if i < len(cells) else "") for d, i in districts.items()}
    return rows


def test_permitted_use_table_agrees_with_json():
    rows = table_rows()
    uses = json.loads((ROOT / "data" / "permitted_uses.json").read_text())
    problems = []
    for u in uses:
        name = norm(SUB_ROWS.get(u["use"], u["use"]))
        match = difflib.get_close_matches(name, list(rows), n=1, cutoff=0.72)
        if not match:
            problems.append(f"{u['use']}: no table row")
            continue
        marks = rows[match[0]]
        diff = {d: (u["districts"].get(d) or "", marks.get(d, "")) for d in set(CODES.values())
                if (u["districts"].get(d) or "") != marks.get(d, "")}
        if diff:
            problems.append(f"{u['use']}: json vs table {diff}")
    assert not problems, "\n".join(problems)


def test_no_word_split_across_lines():
    for d in ("markdown", "personnel"):
        for path in (ROOT / d).glob("*.md"):
            assert not re.search(r"[a-z]-\n[a-z]", path.read_text()), path.name


def test_json_indexes_point_at_their_sections():
    import corpus
    for name in ("special_requirements_index.json", "general_provisions_index.json"):
        for e in json.loads((ROOT / "data" / name).read_text()):
            text = (ROOT / "markdown" / ("Chapter-08-Special-Requirements.md" if "special" in name
                                         else "Chapter-02-General-Provisions.md")).read_text()
            line = text.split("\n")[e["line_start"] - 1]
            base, letter = re.fullmatch(r"(\d+\.\d+)([A-Z]?)", e["section"]).groups()
            assert (line.startswith(f"{letter}.") if letter else base in line), (name, e["section"], line)
    for e in json.loads((ROOT / "data" / "personnel_index.json").read_text()):
        s = corpus.find("personnel", e["id"])
        assert s and s.line == e["line_start"] and s.file == e["filename"], e["id"]
