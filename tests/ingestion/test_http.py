import httpx
import respx
from tenacity import wait_none

from ingestion._http import get_with_retry


@respx.mock
def test_honours_retry_after_on_429() -> None:
    respx.get("https://x.test/").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(429, headers={"Retry-After": "soon"}),
            httpx.Response(200, json={}),
        ]
    )
    sleeps: list[float] = []
    with httpx.Client() as c:
        get_with_retry(c, "https://x.test/", {}, wait=wait_none(), sleep=sleeps.append)
    assert sleeps == [7.0, 0.0]
