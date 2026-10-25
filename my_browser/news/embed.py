"""Sentence embeddings for merging posts into stories, computed in a child process.

The model (fastembed: ONNX, no PyTorch) takes a few hundred megabytes while it works. A child process gives them
back as soon as a batch is done, so the always-on Railway service does not keep them. The model is downloaded
once into REPORTS_DIR/.models, on the volume; NEWS_MODELS_DIR points elsewhere (a throwaway REPORTS_DIR of a local
check reuses the one already downloaded).

    python -m my_browser.news.embed <model> < texts.json > vectors.f32   # the child's side
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

from my_browser.common.settings import REPORTS_DIR, ROOT

# Multilingual, trained on paraphrases and translations: a Russian retelling lands next to the English original.
# Chosen by eval/news (eval/news/results/2026-10-24_2348_minilm.md; mpnet, five times bigger, merged no better).
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CACHE = Path(os.environ.get("NEWS_MODELS_DIR") or REPORTS_DIR / ".models")
TIMEOUT_SEC = 900  # the first run downloads the model (220 MB)
TEXT_CHARS = 300


def text(title: str, body: str) -> str:
    """What a post is embedded by: its title and the start of its text (a Telegram post's title is its first line)."""
    title, body = title.strip(), " ".join((body or "").split())
    if body.startswith(title[:40]):
        return body[:TEXT_CHARS + len(title)]
    return f"{title}. {body[:TEXT_CHARS]}" if body else title


def vectors(texts: list[str], model: str = MODEL) -> np.ndarray:
    """Normalized embeddings, one float32 row per text."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    result = subprocess.run([sys.executable, "-m", "my_browser.news.embed", model], input=json.dumps(texts).encode(),
                            capture_output=True, timeout=TIMEOUT_SEC, cwd=ROOT, env=os.environ.copy())
    if result.returncode != 0:
        raise RuntimeError(f"embedding failed: {result.stderr.decode(errors='replace')[-500:]}")
    return np.frombuffer(result.stdout, dtype=np.float32).reshape(len(texts), -1)


def _main() -> None:
    from fastembed import TextEmbedding

    texts = json.loads(sys.stdin.buffer.read())
    embedder = TextEmbedding(sys.argv[1] if len(sys.argv) > 1 else MODEL, cache_dir=str(CACHE), threads=1)
    rows = np.array(list(embedder.embed(texts, batch_size=32)), dtype=np.float32)
    rows /= np.linalg.norm(rows, axis=1, keepdims=True) + 1e-9
    sys.stdout.buffer.write(rows.tobytes())


if __name__ == "__main__":
    _main()
