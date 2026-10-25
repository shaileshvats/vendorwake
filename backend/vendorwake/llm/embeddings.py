"""Open-weight text embeddings, computed in-process (no API key, no per-call cost).

Uses fastembed's ONNX runtime with `settings.embedding_model` (default BAAI/bge-small-en-v1.5, 384 dims).
The model weights are downloaded once on first use and cached under `FASTEMBED_CACHE_PATH` (or fastembed's
default cache dir). The vector dimension must match the `vector(...)` column created by the migrations.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable

from vendorwake.core.config import Settings


class EmbeddingDimensionError(ValueError):
    pass


class FastEmbedEmbedder:
    def __init__(self, model_name: str, dim: int) -> None:
        self.model_name = model_name
        self.dim = dim
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding  # imported lazily: heavy (onnxruntime)

                self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed(self, texts: Iterable[str]) -> list[list[float]]:
        vectors = [[float(x) for x in v] for v in self._load().embed(list(texts))]
        for v in vectors:
            if len(v) != self.dim:
                raise EmbeddingDimensionError(
                    f"{self.model_name} returned {len(v)} dims, expected {self.dim}"
                )
        return vectors


_cache: dict[tuple[str, int], FastEmbedEmbedder] = {}


def from_settings(settings: Settings) -> FastEmbedEmbedder:
    key = (settings.embedding_model, settings.embedding_dim)
    if key not in _cache:
        _cache[key] = FastEmbedEmbedder(*key)
    return _cache[key]
