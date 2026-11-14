"""Landing-zone storage: local files or Azure Blob (Azurite locally)."""

import gzip
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Protocol

from azure.storage.blob import BlobServiceClient

from common.config import Settings


class Storage(Protocol):
    def write_bytes(self, key: str, data: bytes) -> None: ...

    def read_bytes(self, key: str) -> bytes: ...

    def list_keys(self, prefix: str) -> list[str]: ...

    def exists(self, key: str) -> bool: ...


class FileStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def list_keys(self, prefix: str) -> list[str]:
        if not self.root.exists():
            return []
        keys = (p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        return sorted(k for k in keys if k.startswith(prefix))

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class AzureBlobStorage:
    def __init__(self, connection_string: str, container: str) -> None:
        service = BlobServiceClient.from_connection_string(connection_string)
        self._container = service.get_container_client(container)
        if not self._container.exists():
            self._container.create_container()

    def write_bytes(self, key: str, data: bytes) -> None:
        self._container.upload_blob(key, data, overwrite=True)

    def read_bytes(self, key: str) -> bytes:
        data: bytes = self._container.download_blob(key).readall()
        return data

    def list_keys(self, prefix: str) -> list[str]:
        return sorted(b.name for b in self._container.list_blobs(name_starts_with=prefix))

    def exists(self, key: str) -> bool:
        return bool(self._container.get_blob_client(key).exists())


def get_storage(settings: Settings) -> Storage:
    if settings.storage_backend == "azure":
        if not settings.azure_storage_connection_string:
            raise ValueError(
                "AZURE_STORAGE_CONNECTION_STRING is required when STORAGE_BACKEND=azure"
            )
        return AzureBlobStorage(
            settings.azure_storage_connection_string, settings.landing_container
        )
    return FileStorage(settings.landing_dir)


def write_jsonl_gz(storage: Storage, key: str, records: Iterable[dict[str, Any]]) -> None:
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    storage.write_bytes(key, gzip.compress(payload.encode("utf-8")))


def read_jsonl_gz(storage: Storage, key: str) -> Iterator[dict[str, Any]]:
    text = gzip.decompress(storage.read_bytes(key)).decode("utf-8")
    # split on "\n" only: str.splitlines() also splits on U+2028, U+0085 etc., which are
    # legal unescaped inside JSON strings.
    for line in text.split("\n"):
        if line.strip():
            yield json.loads(line)
