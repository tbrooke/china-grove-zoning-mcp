"""Every question in evals/questions.json must be answered by its tool.

The eval is a regression test as much as a measurement: a change to the text,
the index or the synonyms that loses a correct answer fails here.
"""

import json
import re
from pathlib import Path

import pytest

import server
from evals.run import normalize

QUESTIONS = json.loads((Path(__file__).parent / "evals" / "questions.json").read_text())


@pytest.mark.parametrize("q", QUESTIONS, ids=[q["id"] for q in QUESTIONS])
def test_question_is_answered(q):
    out = getattr(server, q["tool"])(*q["args"])
    assert re.search(q["expect"], normalize(out)), f"{q['tool']}{tuple(q['args'])} lost its answer"
    assert len(out) < 12_000, "answer is crowding the client's context"


def test_every_udo_section_number_resolves():
    import corpus
    udo = [s for s in corpus.all_sections() if s.corpus == "udo" and s.sid]
    assert len(udo) > 500
    for s in udo:
        assert corpus.find("udo", s.sid) is not None, s.sid
