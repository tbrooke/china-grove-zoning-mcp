"""Retrieval eval: does each tool's answer contain the text that answers the question?

Each question in questions.json names a tool, its arguments, and a regex the tool's
output must contain. The regex is matched against the output lowercased, with
whitespace collapsed and "word- word" rejoined, so a fact counts as found whether
it arrives as reflowed prose or as a table row.

    uv run python evals/run.py            # all questions
    uv run python evals/run.py -v         # also show what came back for failures
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import server  # noqa: E402

QUESTIONS = json.loads((Path(__file__).parent / "questions.json").read_text())
BLOATED = 12_000  # chars: past this an answer is crowding the model's context


def normalize(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"(\w)- (\w)", r"\1-\2", text)
    return re.sub(r"\s+", " ", text)


def main(verbose: bool) -> int:
    passed, total_chars = 0, 0
    for q in QUESTIONS:
        tool = getattr(server, q["tool"], None)
        if tool is None:
            print(f"  SKIP  {q['id']:22s} ({q['tool']} not present)")
            continue
        out = tool(*q["args"])
        total_chars += len(out)
        ok = re.search(q["expect"], normalize(out)) is not None
        passed += ok
        flag = " BLOATED" if len(out) > BLOATED else ""
        print(f"  {'PASS' if ok else 'FAIL'}  {q['id']:22s} {len(out):7,d} chars{flag}")
        if verbose and not ok:
            print("        " + normalize(out)[:300])
    print(f"\n{passed}/{len(QUESTIONS)} passed, {total_chars:,} chars returned in total")
    return 0 if passed == len(QUESTIONS) else 1


if __name__ == "__main__":
    sys.exit(main("-v" in sys.argv))
