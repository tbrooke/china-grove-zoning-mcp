"""Embed every search chunk and save the vectors to data/embeddings.npz.

Run after any change to the ordinance text (after clean_text.py and
reindex_lines.py). The server checks the saved fingerprint against the text
and re-embeds in memory when they differ, so a stale file is slow, not wrong.

    uv run python scripts/build_embeddings.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import semantic  # noqa: E402

t = time.time()
n, path = semantic.build()
print(f"{n} chunks embedded with {semantic.MODEL} in {time.time() - t:.1f}s -> {path}")
