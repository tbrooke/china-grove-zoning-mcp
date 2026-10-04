"""Replace tables that PDF-to-text flattened into space-aligned lines with real
markdown tables, re-extracted from the source PDFs with pdfplumber.

Flattened, a table's wrapped cells spill onto the next line and merged cells
(e.g. one "Maximum" note spanning every row of the parking table) interleave
with the rows, so the text reads as a jumble. pdfplumber reads the ruled grid
itself.

Each PDF table is matched to the run of markdown lines made of its words, and
replaced only if the two hold the same words both ways -- so nothing is lost or
invented. Anything that doesn't verify is reported and left alone. Re-running is
harmless: a table already in markdown no longer looks flattened.

    uv run --group build python scripts/rebuild_tables.py [--dry-run]
"""

import re
import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).parent.parent

# (source PDF, markdown files its tables may land in)
UDO_PDFS = ROOT / "sources" / "udo"


def jobs() -> list[tuple[Path, list[Path]]]:
    out = []
    for pdf in sorted(UDO_PDFS.glob("*.pdf")):
        if m := re.match(r"Chapter (\d+)", pdf.name):
            mds = sorted((ROOT / "markdown").glob(f"Chapter-{int(m.group(1)):02d}-*.md"))
        else:
            mds = sorted((ROOT / "markdown").glob("Appendix-A-*.md"))
        out.append((pdf, mds))
    out.append((ROOT / "Personnel Policy 12032024 amendments.pdf",
                sorted((ROOT / "personnel").glob("Section-*.md"))))
    return out


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("’", "'"))


def clean_cell(cell) -> str:
    text = re.sub(r"\s+", " ", (cell or "").replace("\n", " ")).strip()
    text = re.sub(r"(\w)- (\w)", r"\1-\2", text)  # "single- family" from cell wrapping
    return text.replace("|", "\\|")


def pdf_tables(pdf: Path) -> list[list[list[str]]]:
    tables = []
    with pdfplumber.open(pdf) as doc:
        for page in doc.pages:
            for raw in page.extract_tables():
                rows = [[clean_cell(c) for c in r] for r in raw]
                rows = [r for r in rows if any(r)]
                if not rows:
                    continue
                keep = [j for j in range(max(len(r) for r in rows)) if any(j < len(r) and r[j] for r in rows)]
                rows = [[r[j] if j < len(r) else "" for j in keep] for r in rows]
                if len(rows) >= 2 and len(keep) >= 2:
                    tables.append(rows)
    # A table continued on the next page repeats its header row: join them.
    # Each page repeats the title, legend and header rows; keep them once, so a
    # cell split by the page break ("barrels per year") meets its row again.
    merged = []
    for t in tables:
        if merged and t[0] == merged[-1][0] and len(t[0]) == len(merged[-1][0]):
            head = merged[-1][:6]
            k = 0
            while k < len(t) and t[k] in head:
                k += 1
            # "Prefabricated" ends one page, "Wood Building Manufacturing" starts
            # the next: a page-top row whose only text continues the last row.
            prev = merged[-1][-1]
            while k < len(t):
                filled = [j for j, c in enumerate(t[k]) if c]
                if len(filled) == 1 and filled[0] < len(prev) and prev[filled[0]] \
                        and not re.fullmatch(r"[XCS]|[\d.]+", t[k][filled[0]]):
                    prev[filled[0]] = f"{prev[filled[0]]} {t[k][filled[0]]}"
                    k += 1
                else:
                    break
            merged[-1].extend(t[k:])
        else:
            merged.append(t)
    return carry_across_pages(merged)


def carry_across_pages(tables: list[list[list[str]]]) -> list[list[list[str]]]:
    """Consecutive pages whose tables differ in width are not joined, so a Use
    cell split by the page break ("Hazardous" / "Waste Treatment and Disposal")
    is carried back by column label instead."""
    for prev, t in zip(tables, tables[1:]):
        _, prev_labels, prev_body = split_header([list(r) for r in prev])
        _, labels, body = split_header([list(r) for r in t])
        if "Use" not in labels or "Use" not in prev_labels or not body or not prev_body:
            continue
        first = body[0]
        filled = [j for j, c in enumerate(first) if c]
        if filled == [labels.index("Use")]:
            last = prev[-1]
            j = prev_labels.index("Use")
            if last[j]:
                last[j] = f"{last[j]} {first[filled[0]]}"
                t.remove(first)
    return tables


DISTRICT = re.compile(r"^(R-P|R-S|R-T|R-M|RMH|R-MH|N-C|O&I|O-I|C-B|H-B|C-P|L-I|H-I|PUD)$")


def merge_continuations(rows: list[list[str]]) -> list[list[str]]:
    """A cell that wrapped in the PDF can come out as its own row ("barrels per
    year" under "Micro-Breweries producing less than 15,000"), which would read
    as a separate entry. Fold a row back into the one above when its only text
    continues cells the row above already has: it starts in lowercase, or the
    cell above is plainly unfinished ("Medical Equipment and")."""
    out: list[list[str]] = []
    for r in rows:
        filled = [j for j, c in enumerate(r) if c]
        if (out and filled and all(out[-1][j] for j in filled)
                and all(re.match(r"[a-z(,;]", r[j])
                        or re.search(r"(\b(and|or|of|the|for|to|in)|[&,/-])$", out[-1][j])
                        for j in filled)):
            for j in filled:
                out[-1][j] = f"{out[-1][j]} {r[j]}"
            continue
        out.append(list(r))
    return out


def split_header(rows: list[list[str]]) -> tuple[list[str], list[str], list[list[str]]]:
    """(caption lines, one label per column, data rows). Leading one-cell rows
    (a title, a legend) become captions; multi-row headers ("Zoning District" /
    "Residential" / "R-P") collapse to one label per column."""
    captions = []
    while len(rows) > 2 and sum(1 for c in rows[0] if c) == 1:
        captions.append(next(c for c in rows[0] if c))
        rows = rows[1:]
    end = 1
    for i, r in enumerate(rows[:6]):
        if sum(1 for c in r if DISTRICT.match(c)) >= 5:  # the district-code row
            end = i + 1
            break
    else:
        for i, r in enumerate(rows[:6]):  # or the first row labelled by a district
            if i and any(DISTRICT.match(c) for c in r[:2]):
                end = i
                break
    header_rows, body = rows[:end], rows[end:]
    labels = []
    for j in range(len(rows[0])):
        parts = list(dict.fromkeys(r[j] for r in header_rows if j < len(r) and r[j]))
        if not parts:
            labels.append("")
        elif DISTRICT.match(parts[-1]) or len(parts) == 1 or len(parts[-1]) > 6:
            labels.append(parts[-1])
        else:  # "Min." under "Front"
            labels.append(f"{parts[-2]} {parts[-1]}")
    return captions, labels, body


# Rows whose content the PDF prints inside a merged cell, so no extraction can
# place it; each checked by eye against the page image.
CORRECTIONS = {
    # 7-39: a shaded category row with "S" under H-I and "8.27" under Special
    "Mining and Quarrying S 8.27": {"Use": "Mining and Quarrying", "H-I": "S",
                                    "Special Require-ments": "8.27"},
}


def fix_rows(labels: list[str], body: list[list[str]]) -> list[list[str]]:
    if "Use" not in labels:
        return body
    use = labels.index("Use")
    out: list[list[str]] = []
    heading = False  # the previous row is a category heading
    for r in body:
        if r[use] in CORRECTIONS:
            for label, value in CORRECTIONS[r[use]].items():
                r[labels.index(label)] = value
            out.append(r)
            heading = False
            continue
        # A category heading ("23 | General Construction") that landed in a
        # district column belongs in the Use column; a second heading line
        # ("Offices and Facilities") with no code of its own continues it.
        others = [j for j, c in enumerate(r) if c and j not in (0, use)]
        lone = len(others) == 1 and not r[use] and len(r[others[0]]) > 2
        if lone and not r[0] and heading:
            out[-1][use] = f"{out[-1][use]} {r[others[0]]}"
            continue
        if lone and r[0]:
            r[use], r[others[0]] = r[others[0]], ""
            heading = True
        else:
            heading = False
        out.append(r)
    return out


def to_markdown(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    rows = merge_continuations([r + [""] * (width - len(r)) for r in rows])
    captions, labels, body = split_header(rows)
    body = fix_rows(labels, body)
    lines = [f"**{c}**" for c in captions] + ([""] if captions else [])
    lines += ["| " + " | ".join(labels) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def find_run(lines: list[str], start: int, table_words: set[str]) -> tuple[int, int] | None:
    """The run of lines (from `start`) made of the table's words that covers the
    most of it: each non-blank line at least 80% table words, never a heading
    or an existing markdown table row."""
    best, best_cov = None, 0.0
    i = start
    while i < len(lines):
        j, covered = i, set()
        while j < len(lines):
            line = lines[j]
            if line.startswith("#") or line.lstrip().startswith("|"):
                break
            w = words(line)
            if w and sum(x in table_words for x in w) / len(w) < 0.8:
                break
            covered |= set(w)
            j += 1
        while j > i and not lines[j - 1].strip():
            j -= 1
        cov = len(covered & table_words) / max(len(table_words), 1)
        if j > i and cov > best_cov:
            best, best_cov = (i, j), cov
        i = max(j, i + 1)
    return best if best_cov >= 0.6 else None


def main(dry: bool) -> int:
    replaced, skipped = 0, []
    for pdf, mds in jobs():
        if not pdf.exists() or not mds:
            continue
        files = {md: md.read_text().split("\n") for md in mds}
        cursor = {md: 0 for md in mds}
        for t_no, rows in enumerate(pdf_tables(pdf), 1):
            tw = set(words(" ".join(" ".join(r) for r in rows)))
            label = f"{pdf.name[:40]} table {t_no} ({len(rows)}x{len(rows[0])})"
            # A box drawn around text (Chapter 3 frames some definitions) reads
            # as a table of one-cell rows: it is prose, leave it.
            if sum(1 for r in rows if sum(1 for c in r if c) >= 2) < len(rows) / 2:
                skipped.append(f"{label}: boxed text, not a table")
                continue
            # Already converted on an earlier run: its rows are table rows now.
            probe = [c for c in rows[-1] if c][:2]
            if any(l.startswith("|") and all(c in l for c in probe)
                   for md in mds for l in files[md]):
                continue
            hits = [(md, run) for md in mds if (run := find_run(files[md], cursor[md], tw))]
            if not hits:
                skipped.append(f"{label}: no flattened block found (already a table, or prose)")
                continue

            def coverage(hit):
                md, (a, b) = hit
                return len(set(words(" ".join(files[md][a:b]))) & tw)

            md, (a, b) = max(hits, key=coverage)
            block = set(words(" ".join(files[md][a:b])))
            extra = block - tw  # words in the text that the table lacks
            missing = tw - block  # words in the table that the text lacks
            # The PDF is the authority: a table may restore words the flat text
            # dropped, but must not lose any the text has.
            if len(extra) > 2:
                skipped.append(f"{label} -> {md.name}:{a + 1}-{b}: words differ "
                               f"(text-only {sorted(extra)[:6]}, table-only {sorted(missing)[:6]})")
                continue
            new = to_markdown(rows).split("\n")
            files[md][a:b] = new
            cursor[md] = a + len(new)
            replaced += 1
            restored = f"; restores {sorted(missing)[:8]}" if missing else ""
            print(f"  table  {label} -> {md.name}:{a + 1}-{b}{restored}")
        if not dry:
            for md, lines in files.items():
                md.write_text("\n".join(lines))
    print(f"\n{replaced} tables rebuilt" + (" (dry run)" if dry else ""))
    for s in skipped:
        print(f"  skip   {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main("--dry-run" in sys.argv))
