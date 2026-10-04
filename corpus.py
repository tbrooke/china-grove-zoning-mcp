"""Ordinance text as numbered sections, with ranked full-text search.

Every body of law this server answers from -- the UDO, the Town Code of
Ordinances, NCGS 160D and the Personnel Policies -- is markdown with numbered
headings. This module splits each into sections keyed by their official number
(UDO "10.2.1", Code "26-81", "160D-108", Personnel "IV-17.0"), so a caller -- or
another system, such as a GIS that links parcels to the ordinance -- can cite
and fetch exact text by number.

Search is SQLite FTS5 (in the standard library): porter stemming, so "fences"
finds "fence"; whole-word matching, so "ADU" no longer matches "adult"; and
BM25 ranking, so the most relevant section comes first instead of the first
file alphabetically. Queries are built from the content words of the question
plus synonyms from data/search_synonyms.json ("airbnb" -> "bed and breakfast").
Results are whole sections, or for long ones the paragraphs and table rows that
match, never a fixed-length cut.
"""

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).parent

CORPORA = {
    "udo": {"dir": ROOT / "markdown", "label": "UDO"},
    "code": {"dir": ROOT / "ordinances", "label": "Code of Ordinances"},
    "160d": {"dir": ROOT / "statutes", "label": "NCGS 160D"},
    "personnel": {"dir": ROOT / "personnel", "label": "Personnel Policy"},
}

ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]


@dataclass
class Section:
    corpus: str
    sid: str | None  # official number: "10.2.1", "26-81", "160D-108", "IV-17.0"
    title: str  # heading text without its number
    heading: str  # heading as written
    level: int  # markdown heading level; 0 for text before the first heading
    file: str
    line: int  # 1-based line of the heading
    body: str  # the section's own text, without its subsections
    parents: tuple[str, ...]  # ancestor headings, outermost first

    @property
    def ref(self) -> str:
        """How to cite it: "UDO §10.2.1", "Code §26-81", "NCGS 160D-108"."""
        label = CORPORA[self.corpus]["label"]
        if not self.sid:
            return f"{label} — {self.heading}"
        if self.corpus == "160d":
            return f"NCGS {self.sid}"
        if self.corpus == "personnel":
            return f"Personnel Policy {self.sid}"
        if self.sid.startswith(("Chapter ", "Appendix ")):
            return f"{label} {self.sid}"
        return f"{label} §{self.sid}"


# --- Parsing ---------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def _split_heading(corpus: str, heading: str, file: str) -> tuple[str | None, str]:
    """(official number, title) for one heading in a given corpus."""
    h = heading.strip()
    if corpus == "udo":
        if m := re.match(r"^CHAPTER\s+(\d+)[:.]?\s*(.*)$", h, re.I):
            return f"Chapter {int(m.group(1))}", m.group(2).title()
        if m := re.match(r"^APPENDIX\s+([A-Z])[:.]?\s*(.*)$", h, re.I):
            return f"Appendix {m.group(1).upper()}", m.group(2).title()
        if m := re.match(r"^(?:Section\s+)?(\d+(?:\.\d+)+|[A-Z](?:\.\d+)+)\.?\s*(.*)$", h):
            chapter = re.match(r"Chapter-(\d+)", file)
            if chapter and m.group(1)[0].isdigit() and int(m.group(1).split(".")[0]) != int(chapter.group(1)):
                return None, h  # a sentence that began "Section 17.12 ..." mis-read as a heading
            return m.group(1), m.group(2)
    elif corpus == "code":
        if m := re.match(r"^Sec\.\s*(\d+-[\d.]+?)\.?\s+(.*)$", h):
            return m.group(1), m.group(2).rstrip(".")
    elif corpus == "160d":
        if m := re.match(r"^(160D-[\d.]+)[:.]?\s*(.*)$", h):
            return m.group(1), m.group(2)
    elif corpus == "personnel":
        if m := re.match(r"^(\d+\.\d+)\s+(.*)$", h):
            if n := re.match(r"Section-(\d+)", file):
                return f"{ROMAN[int(n.group(1)) - 1]}-{m.group(1)}", m.group(2)
    return None, h


def _strip_frontmatter(lines: list[str]) -> tuple[list[str], int, str]:
    """(lines after frontmatter, lines skipped, frontmatter title)."""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                title = next((re.sub(r'^title:\s*"?|"$', "", l) for l in lines[1:i]
                              if l.startswith("title:")), "")
                return lines[i + 1:], i + 1, title
    return lines, 0, ""


def parse_file(corpus: str, path: Path) -> list[Section]:
    lines, offset, doc_title = _strip_frontmatter(path.read_text().split("\n"))
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []  # (level, heading) of open ancestors
    cur = dict(sid=None, title=doc_title or path.stem, heading=doc_title or path.stem,
               level=0, line=offset + 1, parents=())
    body: list[str] = []

    def close():
        text = "\n".join(body).strip()
        if text or cur["level"] > 0:
            sections.append(Section(corpus=corpus, file=path.name, body=text, **cur))

    for i, raw in enumerate(lines):
        m = _HEADING.match(raw)
        if not m:
            body.append(raw)
            continue
        close()
        level, heading = len(m.group(1)), m.group(2)
        while stack and stack[-1][0] >= level:
            stack.pop()
        sid, title = _split_heading(corpus, heading, path.name)
        cur = dict(sid=sid, title=title, heading=heading, level=level,
                   line=offset + i + 1, parents=tuple(h for _, h in stack))
        stack.append((level, heading))
        body = []
    close()
    return sections


@lru_cache(maxsize=1)
def all_sections() -> tuple[Section, ...]:
    out: list[Section] = []
    for corpus, spec in CORPORA.items():
        if spec["dir"].is_dir():
            for path in sorted(spec["dir"].glob("*.md")):
                out.extend(parse_file(corpus, path))
    return tuple(out)


def subtree(section: Section) -> list[Section]:
    """The section and every subsection under it, in document order."""
    secs = all_sections()
    i = secs.index(section)
    out = [section]
    for s in secs[i + 1:]:
        if s.file != section.file or (s.level <= section.level and section.level > 0):
            break
        out.append(s)
    return out


def render(sections: list[Section]) -> str:
    parts = []
    for s in sections:
        head = f"{'#' * max(s.level, 1)} {s.heading}" if s.level else ""
        parts.append("\n".join(p for p in (head, s.body) if p))
    return "\n\n".join(parts).strip()


def find(corpus: str, sid: str) -> Section | None:
    """A section by official number, ignoring case and a leading "Section"/"§"."""
    key = normalize_sid(sid)
    for s in all_sections():
        if s.corpus == corpus and s.sid and normalize_sid(s.sid) == key:
            return s
    return None


def normalize_sid(sid: str) -> str:
    s = re.sub(r"^\s*(section|sec\.?|§+)\s*", "", sid.strip(), flags=re.I)
    return s.rstrip(".").lower()


# --- Query building ----------------------------------------------------------

STOPWORDS = set("""
a about above after again against all allowed am an and any are as at be because been before
being below between both but by can could did do does doing done down during each few for from
further get gets got had has have having he her here hers him his how i if in into is it its
itself just me more most must my myself need needed needs no nor not now of off on once only or
other our ours out over own per please required requirement requirements rule rules same she
should so some such than that the their theirs them then there these they this those through to
too under until up very want was we were what when where which while who whom why will with
would you your yours tell much many town china grove nc
""".split())

_SYNONYMS_PATH = ROOT / "data" / "search_synonyms.json"


@lru_cache(maxsize=1)
def synonyms() -> dict[str, list[str]]:
    if not _SYNONYMS_PATH.exists():
        return {}
    return {k: v for k, v in json.loads(_SYNONYMS_PATH.read_text()).items() if not k.startswith("_")}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def stem(word: str) -> str:
    """A light stemmer for matching inside excerpts and use names (FTS5 does its own)."""
    w = word.lower()
    for suffix, repl in (("ies", "y"), ("ing", ""), ("es", "e"), ("ed", ""), ("s", "")):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3 and not w.endswith("ss"):
            return w[: -len(suffix)] + repl
    return w


def query_terms(query: str) -> tuple[list[str], list[str]]:
    """(FTS5 terms, plain content words) for a natural-language or keyword query."""
    q = query.lower().strip()
    phrases: list[str] = []
    for key, expansions in synonyms().items():
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", q):
            phrases.extend(expansions)
    # District codes such as "N-C" or "R-MH" are one term, not two letters.
    for code in re.findall(r"\b[a-z]-[a-z]{1,2}\b", q):
        phrases.append(code.replace("-", " "))
    q_wo_codes = re.sub(r"\b[a-z]-[a-z]{1,2}\b", " ", q)
    content = [w for w in _words(q_wo_codes) if w not in STOPWORDS and (len(w) > 1 or w.isdigit())]
    terms = [f'"{w}"' for w in dict.fromkeys(content)]
    terms += [f'"{p}"' for p in dict.fromkeys(" ".join(_words(p)) for p in phrases) if p]
    if len(content) >= 2:
        terms.append(f'"{" ".join(content)}"')  # whole-phrase match ranks highest
    return list(dict.fromkeys(terms)), content + [w for p in phrases for w in _words(p)]


# --- Index -------------------------------------------------------------------

_lock = threading.Lock()


def chunks(section: Section, size: int = 800) -> list[str]:
    """The section's text in paragraph-sized pieces for ranking. A long section
    is judged by its best paragraph, not diluted by its length; a long table is
    split by rows, each piece keeping the header so a row still reads."""
    out, cur = [], ""
    for block in _blocks(section.body) or [""]:
        rows = block.split("\n")
        if block.lstrip().startswith("|") and len(block) > size:
            head, rows = rows[:2], rows[2:]
            for i in range(0, len(rows), 8):
                out.append("\n".join(head + rows[i:i + 8]))
            continue
        if cur and len(cur) + len(block) > size:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{block}" if cur else block
    out.append(cur)
    return [c for c in out if c.strip()] or [""]


@lru_cache(maxsize=1)
def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("CREATE VIRTUAL TABLE s USING fts5(title, body, corpus UNINDEXED, "
               "section UNINDEXED, tokenize='porter unicode61')")
    rows = []
    for i, sec in enumerate(all_sections()):
        for piece in chunks(sec):
            rows.append((f"{sec.sid or ''} {sec.title}", piece, sec.corpus, i))
    db.executemany("INSERT INTO s(title, body, corpus, section) VALUES (?, ?, ?, ?)", rows)
    return db


def search(query: str, corpora: list[str], limit: int = 6) -> list[tuple[Section, float]]:
    """Sections ranked by their best-matching chunk (BM25; lower is better)."""
    terms, _ = query_terms(query)
    if not terms:
        return []
    marks = ",".join("?" * len(corpora))
    sql = (f"SELECT section, bm25(s, 4.0, 1.0) AS score FROM s "
           f"WHERE s MATCH ? AND corpus IN ({marks}) ORDER BY score LIMIT ?")
    with _lock:
        rows = _db().execute(sql, (" OR ".join(terms), *corpora, limit * 8)).fetchall()
    best: dict[int, float] = {}
    for section, score in rows:
        best.setdefault(section, score)
    secs = all_sections()
    ranked = sorted(best.items(), key=lambda kv: kv[1])[:limit]
    return [(secs[i], score) for i, score in ranked]


# --- Excerpts ----------------------------------------------------------------

def _blocks(text: str) -> list[str]:
    """Paragraphs, with each markdown table kept as one block."""
    out, cur, in_table = [], [], False
    for line in text.split("\n"):
        is_row = line.lstrip().startswith("|")
        if not line.strip() or is_row != in_table:
            if cur:
                out.append("\n".join(cur))
            cur = [line] if line.strip() else []
            in_table = is_row
            continue
        cur.append(line)
    if cur:
        out.append("\n".join(cur))
    return [b for b in out if b.strip()]


def _hits(text: str, stems: set[str]) -> int:
    return len(stems & {stem(w) for w in _words(text)})


def excerpt(section: Section, words: list[str], budget: int = 1500) -> str:
    """The section's text if short; otherwise the blocks (and table rows) that match."""
    body = section.body
    if len(body) <= budget:
        return body
    stems = {stem(w) for w in words}
    blocks = _blocks(body)
    ranked = sorted(range(len(blocks)), key=lambda i: (-_hits(blocks[i], stems), i))
    chosen, used = [], 0
    for i in ranked:
        block = blocks[i]
        if _hits(block, stems) == 0 and chosen:
            break
        if block.lstrip().startswith("|"):
            rows = block.split("\n")
            keep = rows[:2] + [r for r in rows[2:] if _hits(r, stems)]
            block = "\n".join(keep if len(keep) > 2 else rows)
        if used + len(block) > budget and chosen:
            continue
        chosen.append(i)
        blocks[i] = block
        used += len(block)
    out, prev = [], None
    for i in sorted(chosen):
        if prev is not None and i != prev + 1:
            out.append("…")
        out.append(blocks[i])
        prev = i
    return "\n\n".join(out)


def format_results(query: str, corpora: list[str], limit: int = 6,
                   budget: int = 1500, reader: dict[str, str] | None = None) -> str | None:
    """Ranked results as markdown, or None when nothing matches.

    `reader` maps corpus -> the tool that returns a section's full text, so each
    result says how to read the rest."""
    results = search(query, corpora, limit)
    if not results:
        return None
    _, words = query_terms(query)
    out = []
    for sec, _score in results:
        trail = " › ".join(sec.parents)
        lines = [f"### {sec.ref}" + (f" — {sec.title}" if sec.sid and sec.title else ""),
                 f"*{CORPORA[sec.corpus]['label']} · {sec.file}:{sec.line}"
                 + (f" · {trail}" if trail else "") + "*",
                 "", excerpt(sec, words, budget) or "_(heading only — see subsections)_"]
        if reader and sec.sid and reader.get(sec.corpus):
            lines.append(f"\n*Full text: {reader[sec.corpus]}(\"{sec.sid}\")*")
        out.append("\n".join(lines))
    return "\n\n".join(out)


# --- Definitions (UDO Chapter 3) -------------------------------------------------

@lru_cache(maxsize=1)
def definitions() -> dict[str, tuple[str, str]]:
    """normalized term -> (term as written, definition), from UDO Chapter 3.

    A term is a short capitalized line after a blank line, not ending in
    punctuation, followed directly by its text."""
    path = CORPORA["udo"]["dir"] / "Chapter-03-Definitions.md"
    if not path.exists():
        return {}
    lines = path.read_text().split("\n")
    starts = []
    for i in range(1, len(lines) - 1):
        l = lines[i].strip()
        if (l and not lines[i - 1].strip() and lines[i + 1].strip() and len(l) < 70
                and l[0].isupper() and not l.startswith(("#", "|", "-", "•", "("))
                and not re.search(r"[.:;,]$", l) and not re.match(r"^([A-Z]\.\s|\d)", l)):
            starts.append(i)
    out = {}
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        text = "\n".join(lines[i + 1:end])
        text = re.split(r"\n#{1,6} ", text)[0].strip()
        out[normalize_term(lines[i])] = (lines[i].strip(), text)
    return out


def normalize_term(term: str) -> str:
    return " ".join(stem(w) for w in _words(term))
