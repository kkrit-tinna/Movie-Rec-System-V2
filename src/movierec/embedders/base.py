from abc import ABC, abstractmethod

from movierec.storage.base import ArtifactStore


class BaseEmbedder(ABC):
    name: str  # "tfidf" | "word2vec"

    # Set by fit(); eval/metrics.py reads both to build neighbours.
    matrix = None  # (n, d), sparse or dense
    movie_ids: list | None = None

    @abstractmethod
    def fit(self, texts: list[str], movie_ids: list) -> None: ...

    @abstractmethod
    def transform(self, texts: list[str]): ...  # (n, d), sparse or dense

    @abstractmethod
    def save(self, store: ArtifactStore, run_date: str) -> None: ...

    @classmethod
    @abstractmethod
    def load(cls, store: ArtifactStore, run_date: str) -> "BaseEmbedder": ...

    def coverage_stats(self, texts: list[str]) -> dict[str, float]:
        """Vocabulary-coverage columns for the comparison table.

        Empty by default; a method with no notion of coverage reports nothing
        and the harness writes null for those columns.
        """
        return {}
