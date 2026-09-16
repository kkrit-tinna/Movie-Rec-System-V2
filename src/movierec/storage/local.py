import json
from pathlib import Path

from movierec.storage.base import ArtifactStore


class LocalStore(ArtifactStore):
    def __init__(self, root: str = "./artifacts"):
        self.root = Path(root)

    def _resolve(self, path: str) -> Path:
        return self.root / path

    def put_bytes(self, path: str, data: bytes) -> None:
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)

    def get_bytes(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    def put_json(self, path: str, obj: dict) -> None:
        self.put_bytes(path, json.dumps(obj).encode("utf-8"))

    def get_json(self, path: str) -> dict:
        return json.loads(self.get_bytes(path).decode("utf-8"))

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def list(self, prefix: str) -> list[str]:
        base = self._resolve(prefix)
        if not base.exists():
            return []
        if base.is_file():
            return [prefix]
        return sorted(
            str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()
        )
