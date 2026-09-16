from abc import ABC, abstractmethod

from movierec.storage.base import ArtifactStore


class BaseEmbedder(ABC):
    name: str  # "tfidf" | "word2vec"

    @abstractmethod
    def fit(self, texts: list[str]) -> None: ...

    @abstractmethod
    def transform(self, texts: list[str]): ...  # (n, d), sparse or dense

    @abstractmethod
    def save(self, store: ArtifactStore, run_date: str) -> None: ...

    @classmethod
    @abstractmethod
    def load(cls, store: ArtifactStore, run_date: str) -> "BaseEmbedder": ...
