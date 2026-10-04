"""Semantic retrieval: local embeddings over the same chunks as the keyword index.

Keyword search finds the ordinance's own words; this finds its meaning, so "run
a business out of my house" reaches "home occupations" and "time off when a
relative dies" reaches "death in the immediate family". corpus.search fuses the
two rankings.

Model: BAAI/bge-base-en-v1.5 through fastembed (ONNX Runtime, CPU, no API
key, nothing leaves the machine; ~210 MB, downloaded once to ~/.cache/fastembed).
Chosen by measurement: on evals/questions.json it answered 12 of 13
paraphrased questions alone (bge-small 10, keyword search 6), as well as
mxbai-embed-large at a third of the size.
Every chunk's vector is precomputed by scripts/build_embeddings.py into
data/embeddings.npz with a fingerprint of the text it was computed from; if the
text has changed since, the vectors are recomputed in memory at first use
(several minutes on a laptop) until the file is rebuilt; test_text.py fails
on a stale file so that never ships. If fastembed or the model is
unavailable, nearest() returns nothing and search is keyword-only.
"""

import hashlib
import sys
import threading
from functools import lru_cache
from pathlib import Path

import corpus

MODEL = "BAAI/bge-base-en-v1.5"
CACHE_DIR = Path.home() / ".cache" / "fastembed"
VECTORS = corpus.ROOT / "data" / "embeddings.npz"

_lock = threading.Lock()


def passages() -> list[str]:
    """What gets embedded: each chunk with its section's citation and title, so
    a chunk of a table still says what it is a table of."""
    secs = corpus.all_sections()
    return [f"{secs[i].ref} {secs[i].title}\n{text}"[:2000] for i, _, text in corpus.chunk_table()]


def fingerprint(texts: list[str]) -> str:
    h = hashlib.sha256(MODEL.encode())
    for t in texts:
        h.update(t.encode())
        h.update(b"\0")
    return h.hexdigest()


@lru_cache(maxsize=1)
def model():
    try:
        from fastembed import TextEmbedding
        return TextEmbedding(MODEL, cache_dir=str(CACHE_DIR))
    except Exception as e:  # no package, no network for the first download, ...
        print(f"semantic search unavailable, keyword search only: {e}", file=sys.stderr)
        return None


def embed_passages(texts: list[str]):
    import numpy as np
    vecs = np.array(list(model().passage_embed(texts, batch_size=64)), dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


@lru_cache(maxsize=1)
def vectors():
    import numpy as np
    texts = passages()
    fp = fingerprint(texts)
    if VECTORS.exists():
        saved = np.load(VECTORS)
        if str(saved["fingerprint"]) == fp:
            return saved["vectors"].astype(np.float32)
        print("data/embeddings.npz is stale (the text changed); re-embedding in memory "
              "-- run scripts/build_embeddings.py to save it", file=sys.stderr)
    return embed_passages(texts)


@lru_cache(maxsize=1)
def _corpus_of():
    import numpy as np
    return np.array([c for _, c, _ in corpus.chunk_table()])


FLOOR = 0.45  # cosine similarity below which a match is noise, always dropped
# (corpus.search separately returns nothing when no word of the question occurs
# in the text at all; similarity alone can't separate nonsense from paraphrase:
# measured, real questions' best match scored 0.50-0.89, off-topic 0.42-0.60.)


def nearest(query: str, corpora: list[str], k: int) -> list[tuple[int, float]]:
    """(chunk position, cosine similarity) most similar in meaning, best first,
    above FLOOR."""
    with _lock:
        m = model()
        if m is None:
            return []
        import numpy as np
        vecs = vectors()
        q = np.array(next(iter(m.query_embed([query]))), dtype=np.float32)
    q /= np.linalg.norm(q)
    scores = vecs @ q
    scores[~np.isin(_corpus_of(), corpora)] = -np.inf
    top = np.argpartition(-scores, min(k, len(scores) - 1))[:k]
    return [(int(n), float(scores[n])) for n in top[np.argsort(-scores[top])] if scores[n] >= FLOOR]


def build() -> tuple[int, str]:
    """Embed every chunk and save the vectors with their fingerprint."""
    import numpy as np
    texts = passages()
    vecs = embed_passages(texts)
    np.savez_compressed(VECTORS, vectors=vecs.astype(np.float16),
                        fingerprint=np.array(fingerprint(texts)))
    return len(texts), str(VECTORS.relative_to(corpus.ROOT))
