"""Clean the PDF-converted markdown so it reads as text, not as page layout.

Runs after scripts/rebuild_tables.py (which needs the PDFs) and is safe to
re-run. Three passes over markdown/ (the UDO) and personnel/:

1. Structure -- Appendix A gets real headings (it had none), its page numbers
   ("A-7") and repeated running title go, as does the table of contents a few
   chapters kept before their first section ("8.35 Recyclable ... 8-24"). A
   wrapped sentence that began "Section 17.12 ..." and was mistaken for a
   heading goes back to being text.
2. Borderless tables -- space-aligned column blocks become markdown tables
   (scripts/aligned_tables.py).
3. Reflow -- lines the PDF wrapped at the page margin are joined back into
   paragraphs and list items, and words split across lines are repaired:
   "right-of-\\nway" keeps its hyphen because the text spells "right-of-way"
   elsewhere; "devel-\\nopment" loses it. Only lines that reached the margin
   are joined, so a definition term ("Home Occupation"), a label ("Rear Yard
   Fences:") or a short last line of a paragraph stays its own line.

    uv run python scripts/clean_text.py [--dry-run]
"""

import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import aligned_tables  # noqa: E402

ROOT = Path(__file__).parent.parent
DIRS = [ROOT / "markdown", ROOT / "personnel"]

MARKER = re.compile(r"^\s*(?:[A-Z]|\d{1,3}|[ivxl]+|[a-z])[.)]\s+\S|^\s*\(\s*(?:\w{1,4})\s*\)\s*\S|^\s*[•\-*]\s")
PAGE_REF = re.compile(r"\s{3,}(?:[A-Z]|\d{1,2})-\d{1,3}\s*$")
TOC_ENTRY = re.compile(r"^\s*(?:[A-Z]|\d{1,2})\.\d+\s{2,}\S")


# --- 1. Structure -----------------------------------------------------------------

def split_frontmatter(text: str) -> tuple[str, str]:
    m = re.match(r"(---\n.*?\n---\n)", text, re.S)
    return (m.group(1), text[m.end():]) if m else ("", text)


def fix_structure(name: str, body: str) -> str:
    lines = body.split("\n")
    chapter = re.match(r"Chapter-(\d+)", name)

    # Table of contents before a chapter's first section.
    first = next((i for i, l in enumerate(lines) if l.startswith("## ")), None)
    if first is not None:
        lines = [l for i, l in enumerate(lines)
                 if i >= first or not (TOC_ENTRY.match(l) or PAGE_REF.search(l))]

    # A wrapped sentence beginning "Section 17.12 ..." is not a heading.
    if chapter:
        out = []
        for l in lines:
            m = re.match(r"^(#{2,6}) (?:Section )?(\d+)\.\d", l)
            out.append(l[len(m.group(1)) + 1:] if m and int(m.group(2)) != int(chapter.group(1)) else l)
        lines = out

    # A section title the converter left as a plain line ("7.17.19 Provisions
    # for All Special Flood Hazard Areas ...", "13.3.1 Single-Family ...") would
    # otherwise file its whole section under the heading before it. Its own
    # chapter's number, a capitalised title, no sentence: a heading. A short
    # capitalised line right after it is the title's wrapped remainder.
    if chapter:
        out, i = [], 0
        while i < len(lines):
            l = lines[i]
            m = re.match(r"^(\d+)\.\d+\.\d+(?:\.\d+)* [A-Z][^.]*$", l.rstrip())
            if (m and int(m.group(1)) == int(chapter.group(1))
                    and (i == 0 or not lines[i - 1].strip())):
                title = l.strip()
                nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
                if nxt and len(nxt) < 60 and nxt[0].isupper() and not re.search(r"[.:;]$", nxt):
                    title = f"{title} {nxt}"
                    i += 1
                out.append(f"### {title}")
                i += 1
                continue
            out.append(l)
            i += 1
        lines = out

    if name.startswith("Appendix-A"):
        title = "APPENDIX A: DESIGN STANDARDS FOR SITE INFRASTRUCTURE"
        out, titled = [], False
        for i, l in enumerate(lines):
            s = l.strip()
            if re.fullmatch(r"A-\d{1,2}", s) or TOC_ENTRY.match(l) or PAGE_REF.search(l):
                continue  # page number, table of contents
            if s == "SITE INFRASTRUCTURE" and out and out[-1].startswith("# APPENDIX"):
                continue
            if s.startswith("APPENDIX A: DESIGN STANDARDS FOR"):
                if not titled:
                    out.append(f"# {title}")
                    titled = True
                continue
            if m := re.fullmatch(r"Section (A\.\d+) (\S.{0,70})", s):
                out.append(f"## Section {m.group(1)} {m.group(2)}")
                continue
            if (m := re.fullmatch(r"(A\.\d+\.\d+(?:\.\d+)*) ([A-Z][^.]{0,70})", s)) and not s.endswith("."):
                out.append(f"### {m.group(1)} {m.group(2)}")
                continue
            out.append(l)
        lines = out
    return "\n".join(lines)


# --- 3. Reflow -------------------------------------------------------------------

def is_text(line: str) -> bool:
    s = line.strip()
    return bool(s) and not s.startswith(("#", "|", "**")) and "____" not in s \
        and not aligned_tables.GAP.search(line.rstrip())


def vocabulary(texts: list[str]) -> tuple[Counter, set[str]]:
    words = Counter(w.lower() for t in texts for w in re.findall(r"[A-Za-z]+", t))
    hyphenated = {h.lower() for t in texts for h in re.findall(r"\b[A-Za-z]+(?:-[A-Za-z]+)+\b", t)}
    return words, hyphenated


def join_hyphen(before: str, after: str, words: Counter, hyphenated: set[str]) -> str:
    """Join "…devel-" + "opment…" (dropping the hyphen) or "…right-of-" + "way…"
    (keeping it), by how the rest of the text spells the word."""
    left = re.search(r"([A-Za-z]+(?:-[A-Za-z]+)*)-$", before).group(1)
    right = re.match(r"([A-Za-z]+)", after).group(1)
    compound = f"{left}-{right}".lower()
    closed = f"{left.split('-')[-1]}{right}".lower()
    if compound in hyphenated or "-" in left:
        return f"{before}{after}"
    if words[closed] > 0:
        return f"{before[:-1]}{after}"
    return f"{before}{after}"


def reflow(body: str, words: Counter, hyphenated: set[str]) -> str:
    lines = body.split("\n")
    widths = [len(l.rstrip()) for l in lines if is_text(l)]
    if len(widths) < 20:
        return body
    margin = statistics.quantiles(widths, n=10)[8]  # 90th percentile: the page margin
    wrapped_at = 0.72 * margin

    out: list[str] = []
    i = 0
    while i < len(lines):
        line = re.sub(r"^(\s*(?:[A-Z]|\d{1,3}|[ivxl]+|[a-z])[.)]|\s*[•\-*])\s{2,}(?=\S)", r"\1 ", lines[i])
        line = re.sub(r"(\w)- (?!and\b|or\b|to\b)(\w)", r"\1-\2", line)
        # A blank line in mid-sentence ("unobstructed from the ground" / "" / "upward.")
        if (not line.strip() and out and is_text(out[-1]) and not re.search(r"[.:;!?)]\s*$", out[-1])
                and i + 1 < len(lines) and re.match(r"\s*[a-z]", lines[i + 1]) and is_text(lines[i + 1])):
            i += 1
            continue
        if (out and is_text(line) and not MARKER.match(line) and is_text(out[-1])
                and len(out[-1].rstrip()) >= wrapped_at
                and not (re.search(r"[.:;]$", out[-1].rstrip()) and re.match(r"\s*[A-Z]", line)
                         and len(out[-1].rstrip()) < 0.93 * margin)):
            prev = out[-1].rstrip()
            nxt = line.strip()
            if re.search(r"[A-Za-z]-$", prev) and re.match(r"[a-z]", nxt):
                out[-1] = join_hyphen(prev, nxt, words, hyphenated)
            else:
                out[-1] = f"{prev} {nxt}"
        else:
            out.append(line)
        i += 1
    return "\n".join(out)


def main(dry: bool) -> int:
    files = [p for d in DIRS for p in sorted(d.glob("*.md"))]
    texts = {p: p.read_text() for p in files}
    words, hyphenated = vocabulary(list(texts.values()))
    changed = 0
    for path in files:
        front, body = split_frontmatter(texts[path])
        body = fix_structure(path.name, body)
        body, _ = aligned_tables.convert(body)
        body = reflow(body, words, hyphenated)
        new = front + body
        if new != texts[path]:
            changed += 1
            before, after = texts[path].count("\n"), new.count("\n")
            print(f"  {path.parent.name}/{path.name}: {before} -> {after} lines")
            if not dry:
                path.write_text(new)
    print(f"{changed} files cleaned" + (" (dry run)" if dry else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main("--dry-run" in sys.argv))
