"""Convert the Town of China Grove Personnel Policies & Procedures PDF into
per-section markdown files (personnel/) and a searchable index
(data/personnel_index.json).

Mirrors the town-code pipeline (build_ordinances_index.py): the markdown files
are the canonical full text, the index records every numbered provision and its
line offset so get_personnel_policy_section() can slice it out, and
search_personnel_policy() greps the markdown directly.

Requires the `pdftotext` binary (poppler) on PATH.
"""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
PDF = ROOT / "Personnel Policy 12032024 amendments.pdf"
OUT_DIR = ROOT / "personnel"
INDEX_FILE = ROOT / "data" / "personnel_index.json"

# Ordered sections: (roman numeral, two-digit file number, name).
# Numbering restarts inside each section, so the roman numeral is part of the
# unique id for every provision (e.g. "II-2.08" vs "III-2.08").
SECTIONS = [
    ("I", "01", "Introduction"),
    ("II", "02", "Employment Practices"),
    ("III", "03", "General Personnel Policies"),
    ("IV", "04", "Employee Benefits and Services"),
    ("V", "05", "Equal Employment Opportunity"),
    ("VI", "06", "Wage and Salary Administration"),
    ("VII", "07", "Safety Policy and Procedures"),
    ("VIII", "08", "Implementation of Policy"),
    ("IX", "09", "Pay Grade Classifications and Salary Scale"),
    ("X", "10", "Appendix"),
]

FOOTER_RE = re.compile(
    r"TOWN OF CHINA GROVE PERSONNEL POLICIES AND PROCEDURES\s+Page \| \d+"
)
SECTION_LINE_RE = re.compile(r"^(END OF )?SECTION [IVXLC]+$")
END_MARKER_RE = re.compile(r"^END OF SECTION [IVXLC]+$")
# Numbered provision headers: "1.0  FOREWARD", "2.08  DISQUALIFICATION...",
# "1.04.01 REGULAR FULL-TIME". The title must start with a letter (avoids
# matching figures like "401 (k)" or list items).
HEADER_RE = re.compile(r"^(\d{1,2}\.0|\d{1,2}(?:\.\d{2}){1,3})\s+([A-Za-z].*)$")
# Appendix forms: 'ITEM A)  "REQUEST FOR MILITARY LEAVE"'
ITEM_RE = re.compile(r"^ITEM ([A-Z])\)\s*(.*)$")


def clean_title(title: str) -> str:
    """Strip dotted leaders and trailing 'Page N' left over from any TOC line."""
    title = re.sub(r"[.\s]{2,}Page\s+\d+\s*$", "", title)
    return title.strip().rstrip(".").strip()


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")


def heading_level(number: str) -> int:
    """Markdown heading depth for a provision number.

    1.0 (major) -> H3, 1.03 -> H4, 1.04.01 -> H5, 1.04.01.01 -> H6.
    """
    if re.fullmatch(r"\d{1,2}\.0", number):
        return 3
    return 3 + number.count(".")


def find_section_starts(lines: list[str]) -> dict[str, int]:
    """Find where each section's body begins, in document order.

    Each section has a cover page, then a per-section table of contents, then the
    left-aligned body marker (a form-feed + "SECTION <roman>"). We anchor on the
    body marker so the mini-TOC is skipped. Sections IX and X (pay tables and
    appendix forms) have no body marker, so we fall back to their cover page.
    """
    starts: dict[str, int] = {}
    cursor = 0
    for roman, _num, _name in SECTIONS:
        target = f"SECTION {roman}"
        body_idx = cover_idx = None
        for i in range(cursor, len(lines)):
            c = lines[i].replace("\x0c", "")
            if c.strip() != target:
                continue
            if cover_idx is None:
                cover_idx = i
            if not c.startswith(" "):  # left-aligned == body marker
                body_idx = i
                break
        chosen = body_idx if body_idx is not None else cover_idx
        if chosen is not None:
            starts[roman] = chosen
            cursor = chosen + 1
    return starts


def clean(lines: list[str]) -> list[str]:
    """Drop footers, page-break markers, and running section headers."""
    out = []
    for line in lines:
        line = line.replace("\x0c", "").rstrip()
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        if FOOTER_RE.search(stripped) or SECTION_LINE_RE.match(stripped):
            continue
        out.append(line)
    # Collapse runs of blank lines.
    collapsed = []
    for line in out:
        if line == "" and collapsed and collapsed[-1] == "":
            continue
        collapsed.append(line)
    return collapsed


def build_section(roman, num, name, body_lines):
    """Return (markdown_text, index_entries) for one section."""
    filename = f"Section-{num}-{slug(name)}.md"
    out = [
        "---",
        f'title: "Section {roman} - {name}"',
        'source: "Town of China Grove Personnel Policies and Procedures"',
        "---",
        "",
        f"# SECTION {roman} — {name.upper()}",
        "",
    ]
    entries = []

    for raw in clean(body_lines):
        stripped = raw.strip()
        header = HEADER_RE.match(stripped)
        item = ITEM_RE.match(stripped)

        # A title ending in a comma is a sentence that merely opens with a
        # cross-reference (e.g. "7.02.02 Personal Conduct, an employee may..."),
        # not a real heading — keep it as body text.
        if header and not header.group(2).rstrip().endswith(","):
            number, title = header.group(1), clean_title(header.group(2))
            level = heading_level(number)
            out.append("")
            out.append(f"{'#' * level} {number} {title}")
            line_start = len(out)  # 1-based line of the heading
            out.append("")
            entries.append({
                "section": roman,
                "section_name": name,
                "number": number,
                "id": f"{roman}-{number}",
                "title": title,
                "filename": filename,
                "line_start": line_start,
            })
        elif item:
            letter, title = item.group(1), clean_title(item.group(2)).strip('"“”')
            out.append("")
            out.append(f"### ITEM {letter}) {title}")
            line_start = len(out)
            out.append("")
            entries.append({
                "section": roman,
                "section_name": name,
                "number": f"Item {letter}",
                "id": f"{roman}-Item-{letter}",
                "title": title,
                "filename": filename,
                "line_start": line_start,
            })
        else:
            out.append(raw)

    return filename, "\n".join(out).strip() + "\n", entries


def main():
    raw = subprocess.run(
        ["pdftotext", "-layout", str(PDF), "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    lines = raw.split("\n")

    starts = find_section_starts(lines)
    ordered = [(r, n, nm) for (r, n, nm) in SECTIONS if r in starts]

    # Each section's body ends at its "END OF SECTION <roman>" marker (present for
    # I-VIII). Without one (IX, X), fall back to the next section's start so the
    # next section's cover page and table of contents are never swallowed.
    end_markers = {
        lines[i].replace("\x0c", "").strip().split()[-1]: i
        for i, ln in enumerate(lines)
        if END_MARKER_RE.match(lines[i].replace("\x0c", "").strip())
    }

    OUT_DIR.mkdir(exist_ok=True)
    all_entries = []

    for idx, (roman, num, name) in enumerate(ordered):
        start = starts[roman]
        next_start = starts[ordered[idx + 1][0]] if idx + 1 < len(ordered) else len(lines)
        end = end_markers.get(roman, next_start)
        body = lines[start + 1:end]
        filename, text, entries = build_section(roman, num, name, body)
        (OUT_DIR / filename).write_text(text)
        all_entries.extend(entries)
        print(f"  {filename}: {len(entries)} provisions")

    INDEX_FILE.write_text(json.dumps(all_entries, indent=2))
    print(f"\nWrote {len(all_entries)} entries to {INDEX_FILE}")


if __name__ == "__main__":
    main()
