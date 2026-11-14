from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from common.config import Settings
from common.storage import (
    AzureBlobStorage,
    FileStorage,
    get_storage,
    read_jsonl_gz,
    write_jsonl_gz,
)


def test_file_storage_round_trip(tmp_path: Path) -> None:
    st = FileStorage(tmp_path)
    assert not st.exists("a/b.bin")
    st.write_bytes("a/b.bin", b"hello")
    assert st.exists("a/b.bin")
    assert st.read_bytes("a/b.bin") == b"hello"


def test_file_storage_list_keys_sorted_and_prefixed(tmp_path: Path) -> None:
    st = FileStorage(tmp_path)
    for key in [
        "ctgov/2026-01-02/part-00001.jsonl.gz",
        "ctgov/2026-01-01/part-00000.jsonl.gz",
        "fda/2026-01-01/part-00000.jsonl.gz",
    ]:
        st.write_bytes(key, b"x")
    assert st.list_keys("ctgov/") == [
        "ctgov/2026-01-01/part-00000.jsonl.gz",
        "ctgov/2026-01-02/part-00001.jsonl.gz",
    ]
    assert st.list_keys("nothing/") == []
    assert len(st.list_keys("")) == 3


def test_jsonl_gz_helpers(tmp_path: Path) -> None:
    st = FileStorage(tmp_path)
    records = [{"a": 1}, {"b": [1, 2], "c": "é"}]
    write_jsonl_gz(st, "x/part-00000.jsonl.gz", records)
    assert list(read_jsonl_gz(st, "x/part-00000.jsonl.gz")) == records


def test_get_storage_file(tmp_path: Path) -> None:
    s = Settings(_env_file=None, landing_dir=str(tmp_path))  # type: ignore[call-arg]
    assert isinstance(get_storage(s), FileStorage)


def test_azure_requires_connection_string() -> None:
    s = Settings(_env_file=None, storage_backend="azure")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        get_storage(s)


def test_azure_storage_creates_container_and_delegates() -> None:
    with patch("common.storage.BlobServiceClient") as svc_cls:
        svc = svc_cls.from_connection_string.return_value
        container = MagicMock()
        svc.get_container_client.return_value = container
        container.exists.return_value = False
        st = AzureBlobStorage("conn", "landing")
        svc_cls.from_connection_string.assert_called_once_with("conn")
        container.create_container.assert_called_once()

        st.write_bytes("k", b"v")
        container.upload_blob.assert_called_once_with("k", b"v", overwrite=True)

        container.download_blob.return_value.readall.return_value = b"v"
        assert st.read_bytes("k") == b"v"

        b1, b2 = MagicMock(), MagicMock()
        b1.name, b2.name = "p/b", "p/a"
        container.list_blobs.return_value = [b1, b2]
        assert st.list_keys("p/") == ["p/a", "p/b"]
        container.list_blobs.assert_called_with(name_starts_with="p/")

        container.get_blob_client.return_value.exists.return_value = True
        assert st.exists("k")


def test_azure_storage_existing_container_not_recreated() -> None:
    with patch("common.storage.BlobServiceClient") as svc_cls:
        container = svc_cls.from_connection_string.return_value.get_container_client.return_value
        container.exists.return_value = True
        AzureBlobStorage("conn", "landing")
        container.create_container.assert_not_called()
