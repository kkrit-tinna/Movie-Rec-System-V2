from abc import ABC, abstractmethod


class ArtifactStore(ABC):
    @abstractmethod
    def put_bytes(self, path: str, data: bytes) -> None: ...

    @abstractmethod
    def get_bytes(self, path: str) -> bytes: ...

    @abstractmethod
    def put_json(self, path: str, obj: dict) -> None: ...

    @abstractmethod
    def get_json(self, path: str) -> dict: ...

    @abstractmethod
    def exists(self, path: str) -> bool: ...

    @abstractmethod
    def list(self, prefix: str) -> list[str]: ...
