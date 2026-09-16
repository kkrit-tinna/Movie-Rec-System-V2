from movierec.storage.base import ArtifactStore


class S3Store(ArtifactStore):
    def __init__(self, bucket: str, prefix: str = ""):
        self.bucket = bucket
        self.prefix = prefix

    def put_bytes(self, path: str, data: bytes) -> None:
        raise NotImplementedError

    def get_bytes(self, path: str) -> bytes:
        raise NotImplementedError

    def put_json(self, path: str, obj: dict) -> None:
        raise NotImplementedError

    def get_json(self, path: str) -> dict:
        raise NotImplementedError

    def exists(self, path: str) -> bool:
        raise NotImplementedError

    def list(self, prefix: str) -> list[str]:
        raise NotImplementedError
