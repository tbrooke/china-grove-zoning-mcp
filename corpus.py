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
        roman = ROMAN[int(n.group(1)) - 1] if (n := re.match(r"Section-(\d+)", file)) else None
        if roman and (m := re.match(r"^(\d+(?:\.\d+)+)\s+(.*)$", h)):
            return f"{roman}-{m.group(1)}", m.group(2)
        if roman and (m := re.match(r"^ITEM ([A-Z])\)\s*(.*)$", h, re.I)):
            return f"{roman}-Item-{m.group(1).upper()}", m.group(2)
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
def chunk_table() -> tuple[tuple[int, str, str], ...]:
    """Every searchable chunk as (section index, corpus, text), in one fixed
    order shared by the keyword index (rowid = position) and the embeddings
    (row = position). Empty sections stay readable by number, not searchable."""
    return tuple((i, sec.corpus, piece)
                 for i, sec in enumerate(all_sections()) for piece in chunks(sec)
                 # a heading whose text is all in its subsections, or a
                 # "Reserved" placeholder, has nothing to find
                 if piece.strip() and not re.fullmatch(r"\W*reserved\W*", piece.strip(), re.I))


@lru_cache(maxsize=1)
def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("CREATE VIRTUAL TABLE s USING fts5(title, body, corpus UNINDEXED, "
               "section UNINDEXED, tokenize='porter unicode61')")
    secs = all_sections()
    db.executemany(
        "INSERT INTO s(rowid, title, body, corpus, section) VALUES (?, ?, ?, ?, ?)",
        [(n, f"{secs[i].sid or ''} {secs[i].title}", text, corpus, i)
         for n, (i, corpus, text) in enumerate(chunk_table())])
    return db


def keyword_chunks(query: str, corpora: list[str], k: int) -> list[int]:
    """Chunk positions ranked by BM25 (title words weigh 4x body words)."""
    terms, _ = query_terms(query)
    if not terms:
        return []
    marks = ",".join("?" * len(corpora))
    sql = (f"SELECT rowid FROM s WHERE s MATCH ? AND corpus IN ({marks}) "
           f"ORDER BY bm25(s, 4.0, 1.0) LIMIT ?")
    with _lock:
        return [r[0] for r in _db().execute(sql, (" OR ".join(terms), *corpora, k))]


def _sections_in_order(chunk_positions: list[int]) -> list[int]:
    out: list[int] = []
    table = chunk_table()
    for n in chunk_positions:
        if table[n][0] not in out:
            out.append(table[n][0])
    return out


def _substantive(query: str, chunk_positions: list[int]) -> list[int]:
    """Keyword matches that are about the question, not one stray common word:
    for a question with three or more content words, a chunk must contain two
    of them, or one of the question's synonym phrases. "can I build a second
    small house in my backyard" should not rank "Small Wireless Facilities"."""
    _, words = query_terms(query)
    content = {stem(w) for w in _words(query) if w not in STOPWORDS and len(w) > 1}
    if len(content) < 3:
        return chunk_positions
    phrases = [" ".join(stem(w) for w in _words(p)) for key, ps in synonyms().items()
               if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", query.lower()) for p in ps]
    table, secs = chunk_table(), all_sections()
    keep = []
    for n in chunk_positions:
        i, _, text = table[n]
        stems = [stem(w) for w in _words(f"{secs[i].title} {text}")]
        joined = " ".join(stems)
        if len(content & set(stems)) >= 2 or any(p and p in joined for p in phrases):
            keep.append(n)
    return keep


def search(query: str, corpora: list[str], limit: int = 6) -> list[tuple[Section, float, list[int]]]:
    """(section, score, matching chunk positions), best first.

    Keyword (BM25) and semantic (embedding) search each rank sections; the
    sections both put in their top `limit` come first, then the rest alternate,
    semantic first. So each method's best results always get a place: a
    paraphrased question ("time off when a relative dies") is not outvoted by
    noisy keyword matches, nor an exact term ("R-MH", "160D-108") by vaguer
    semantic ones -- which rank fusion, tried first, did allow, since a section
    both lists merely mention could push either list's top hit out. Chosen by
    measurement on evals/questions.json against keyword-only, semantic-only,
    reciprocal-rank fusion and a cross-encoder reranker.

    Without the embedding model it is keyword ranking alone. A section's
    matching chunks are ordered by their best rank in either list, so its
    excerpt shows the passage that matched."""
    import semantic  # local import: the model loads only when search is used

    depth = limit * 8
    raw = keyword_chunks(query, corpora, depth)
    # If not one word of the question occurs in the text, there is nothing to
    # find: "qwxyzzy" must get no results, not its nearest neighbour. (Every real
    # question in the eval shares some word with its corpus; similarity alone
    # could not tell nonsense, 0.60, from real paraphrases.)
    if not raw:
        return []
    kw_chunks = _substantive(query, raw)
    sem_chunks = [n for n, _ in semantic.nearest(query, corpora, depth)]
    kw, sem = _sections_in_order(kw_chunks), _sections_in_order(sem_chunks)

    both = sorted(set(kw[:limit]) & set(sem[:limit]), key=lambda x: kw.index(x) + sem.index(x))
    order = list(both)
    for pair in zip(sem, kw):
        order.extend(x for x in pair if x not in order)
    order.extend(x for x in sem + kw if x not in order)

    chunk_rank: dict[int, int] = {}
    for ranked in (kw_chunks, sem_chunks):
        for r, n in enumerate(ranked):
            chunk_rank[n] = min(chunk_rank.get(n, r), r)
    table = chunk_table()
    hits: dict[int, list[int]] = {}
    for n in sorted(chunk_rank, key=chunk_rank.get):
        hits.setdefault(table[n][0], []).append(n)

    secs = all_sections()
    return [(secs[i], 1.0 / (k + 1), hits.get(i, [])) for k, i in enumerate(order[:limit])]


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


def excerpt(section: Section, words: list[str], budget: int = 1500,
            matched: list[str] | None = None) -> str:
    """The section's text if short; otherwise the chunks search matched, in
    document order (a paraphrase's answer may share no words with it), or
    failing that the blocks and table rows that share words with the query."""
    body = section.body
    if len(body) <= budget:
        return body
    if matched:
        chosen, used = [], 0
        for text in matched:
            if used + len(text) > budget and chosen:
                break
            chosen.append(text)
            used += len(text)
        return "\n\n…\n\n".join(t for t in chunks(section) if t in chosen)
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
    table = chunk_table()
    for rank, (sec, _score, matched) in enumerate(results):
        room = int(budget * 1.5) if rank < 2 else int(budget * 0.75)  # the best get more text
        trail = " › ".join(sec.parents)
        lines = [f"### {sec.ref}" + (f" — {sec.title}" if sec.sid and sec.title else ""),
                 f"*{CORPORA[sec.corpus]['label']} · {sec.file}:{sec.line}"
                 + (f" · {trail}" if trail else "") + "*",
                 "", excerpt(sec, words, room, [table[n][2] for n in matched])
                 or "_(heading only — see subsections)_"]
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
