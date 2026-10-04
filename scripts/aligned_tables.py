"""Turn space-aligned column blocks (borderless tables that `pdftotext -layout`
kept in columns) into markdown tables.

The personnel policy's tables -- vacation accrual, pay grade classifications,
the salary scale -- have no ruling for pdfplumber to find, but their columns
line up: a column boundary is a run of character positions that is blank on
every line of the block. A block becomes a table only if it has at least three
rows and two columns and every line splits cleanly; list items ("1.   The
variance application") and signature lines are left alone.

    uv run python scripts/aligned_tables.py [--dry-run] DIR...
"""

import re
import sys
from pathlib import Path

GAP = re.compile(r"\S {3,}\S")
VALUES = re.compile(r"[\d\s.,$%/-]+")
LIST_ITEM = re.compile(r"^\s*([A-Z]|\d{1,2}|[ivx]+|[a-z])[.)]\s{2,}\S")


def is_row(line: str) -> bool:
    s = line.rstrip()
    return bool(GAP.search(s)) and not s.lstrip().startswith(("|", "#", "•", "-", "_")) \
        and "____" not in s and not LIST_ITEM.match(s)


def columns(lines: list[str]) -> list[tuple[int, int]] | None:
    width = max(len(l) for l in lines)
    padded = [l.ljust(width) for l in lines]
    blank = [all(p[i] == " " for p in padded) for i in range(width)]
    spans, start = [], None
    for i in range(width + 1):
        filled = i < width and not blank[i]
        if filled and start is None:
            start = i
        elif not filled and start is not None:
            # a single blank column inside a cell ("per year") is not a boundary
            if i < width and i + 1 < width and not blank[i + 1]:
                continue
            spans.append((start, i))
            start = None
    merged = []
    for a, b in spans:
        if merged and a - merged[-1][1] < 2:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged if len(merged) >= 2 else None


def header_index(rows: list[list[str]]) -> int:
    """The header is the first row, near the top, that labels every column
    ("GRADE | START | MINIMUM | ..."); rows above it are captions."""
    for k, r in enumerate(rows[:4]):
        if all(r) and not any(re.search(r"\d", c) for c in r):
            return k
    return 0


def convert(text: str) -> tuple[str, int]:
    lines = text.split("\n")
    out, i, n = [], 0, 0
    while i < len(lines):
        if not is_row(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        # A row whose label wrapped around its values -- "Community Engagement" /
        # "      1      15" / "Sergeant/Traffic Officer" -- joins the table when
        # its middle line holds only values; the two label lines become cell one.
        j, block, wrapped = i, [], {}
        while j < len(lines):
            if is_row(lines[j]):
                block.append(lines[j].rstrip())
                j += 1
            elif (block and j + 2 < len(lines) and lines[j].strip() and lines[j + 2].strip()
                  and not GAP.search(lines[j]) and not GAP.search(lines[j + 2])
                  and VALUES.fullmatch(lines[j + 1].strip() or "x")
                  and lines[j + 1].startswith("   ")
                  and j + 3 < len(lines) and (is_row(lines[j + 3]) or not lines[j + 3].strip())):
                wrapped[len(block)] = f"{lines[j].strip()} {lines[j + 2].strip()}"
                block.append(lines[j + 1].rstrip())
                j += 3
            elif not lines[j].strip() and j + 1 < len(lines) and is_row(lines[j + 1]):
                j += 1
            else:
                break
        cols = columns(block) if len(block) >= 3 else None
        if not cols:
            out.extend(lines[i:j])
            i = j
            continue
        rows = [[re.sub(r"\s+", " ", l[a:b]).strip().replace("|", "\\|") for a, b in cols] for l in block]
        for k, label in wrapped.items():
            rows[k][0] = label
        if any(sum(1 for c in r if c) < 2 for r in rows[1:]) and len(cols) > 2:
            out.extend(lines[i:j])  # ragged: probably wrapped prose, leave it
            i = j
            continue
        h = header_index(rows)
        out.extend(" ".join(c for c in r if c) for r in rows[:h])
        if h:
            out.append("")
        out.append("| " + " | ".join(rows[h]) + " |")
        out.append("|" + "---|" * len(cols))
        out.extend("| " + " | ".join(r) + " |" for r in rows[h + 1:])
        n += 1
        i = j
    return "\n".join(out), n


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    total = 0
    for d in [a for a in argv if not a.startswith("--")]:
        for path in sorted(Path(d).glob("*.md")):
            new, n = convert(path.read_text())
            if n:
                total += n
                print(f"  {n:2d} tables  {path}")
                if not dry:
                    path.write_text(new)
    print(f"{total} aligned tables converted" + (" (dry run)" if dry else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
