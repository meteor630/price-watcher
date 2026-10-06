"""pricewatch.parsers.fetch: защита от SSRF, закрепление адреса, перенаправления, пределы размера и времени.

Сеть не нужна: транспорт подменён и записывает, куда ушёл бы каждый запрос.
"""

import gzip
import ipaddress
from collections.abc import Callable, Iterator

import httpx2
import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.parsers.fetch import MAX_REDIRECTS, FetchResponse, SafeFetcher, is_public_address
from pricewatch.parsers.urls import normalize_url

PUBLIC = ipaddress.ip_address("93.184.216.34")
PUBLIC_2 = ipaddress.ip_address("93.184.216.35")


class Recorder:
    def __init__(self, respond: Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.requests: list[httpx2.Request] = []
        self._respond = respond

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self._respond(request)


def fetcher(respond: Callable[[httpx2.Request], httpx2.Response], resolve: dict[str, list] | Callable | None = None, **kwargs):  # type: ignore[no-untyped-def]
    recorder = Recorder(respond)
    table = resolve if resolve is not None else {}

    def resolver(host: str) -> list:
        if callable(table):
            return table(host)
        return table.get(host, [PUBLIC])

    return SafeFetcher(resolver=resolver, transport=httpx2.MockTransport(recorder), **kwargs), recorder


def ok(body: bytes = b"<html>ok</html>", content_type: str = "text/html; charset=utf-8") -> Callable:
    return lambda request: httpx2.Response(200, content=body, headers={"content-type": content_type})


def fetch(client: SafeFetcher, raw: str) -> FetchResponse:
    return client.fetch(normalize_url(raw))


def code(call: Callable[[], object]) -> ErrorCode:
    with pytest.raises(PriceWatcherError) as caught:
        call()
    return caught.value.code


# --- обычная загрузка и закрепление адреса ---


def test_request_goes_to_checked_address_with_store_name_for_tls() -> None:
    client, recorder = fetcher(ok())

    response = fetch(client, "https://www.shop.example/p/1?b=2&a=1")

    (request,) = recorder.requests
    assert request.url.host == "93.184.216.34"
    assert request.url.path == "/p/1" and request.url.query == b"a=1&b=2"
    assert request.headers["Host"] == "www.shop.example"
    assert request.extensions["sni_hostname"] == "www.shop.example"
    assert response.status == 200 and response.body == b"<html>ok</html>"
    assert response.url.url == "https://www.shop.example/p/1?a=1&b=2"


def test_ipv6_address_is_used_in_brackets() -> None:
    client, recorder = fetcher(ok(), {"shop.example": [ipaddress.ip_address("2a00:1450:4010::1")]})

    fetch(client, "https://shop.example/p")

    assert recorder.requests[0].url.host == "2a00:1450:4010::1"


def test_status_and_body_returned_as_is_for_adapter() -> None:
    client, _ = fetcher(lambda request: httpx2.Response(404, content=b"nope", headers={"content-type": "text/plain"}))

    response = fetch(client, "https://shop.example/p")

    assert (response.status, response.body, response.content_type) == (404, b"nope", "text/plain")


@pytest.mark.parametrize(
    ("content_type", "body", "text"),
    [
        ("text/html; charset=windows-1251", "Лампа".encode("cp1251"), "Лампа"),
        ("text/html", "Лампа".encode(), "Лампа"),
        ("text/html; charset=\"utf-8\"", "Лампа".encode(), "Лампа"),
        ("text/html; charset=no-such-codec", "Лампа".encode(), "Лампа"),
        ("text/html; charset=utf-8", b"\xff\xfe", "��"),
    ],
)
def test_text_decoding(content_type: str, body: bytes, text: str) -> None:
    assert FetchResponse(200, normalize_url("https://shop.example/"), content_type, body).text() == text


def test_proxy_from_environment_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:3128")
    client, recorder = fetcher(ok())

    fetch(client, "https://shop.example/p")

    assert recorder.requests[0].url.host == "93.184.216.34"


# --- внутренние адреса ---


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.5.4",
        "192.168.1.1",  # роутер
        "169.254.169.254",  # метаданные облака
        "100.64.0.1",  # CGNAT провайдера
        "0.0.0.0",
        "255.255.255.255",
        "224.0.0.1",  # многоадресный, хотя ipaddress считает его глобальным
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",  # IPv4 внутри IPv6
        "64:ff9b::a00:1",  # NAT64 на 10.0.0.1
        "2002:a00:1::1",  # 6to4 из 10.0.0.1
    ],
)
def test_internal_address_is_refused_before_any_connection(address: str) -> None:
    client, recorder = fetcher(ok(), {"shop.example": [ipaddress.ip_address(address)]})

    assert code(lambda: fetch(client, "https://shop.example/p")) is ErrorCode.INVALID_URL
    assert recorder.requests == []


def test_one_internal_address_among_public_ones_is_refused() -> None:
    client, recorder = fetcher(ok(), {"shop.example": [PUBLIC, ipaddress.ip_address("192.168.1.1")]})

    assert code(lambda: fetch(client, "https://shop.example/p")) is ErrorCode.INVALID_URL
    assert recorder.requests == []


def test_public_addresses_pass() -> None:
    for address in ("8.8.8.8", "93.184.216.34", "2a00:1450:4010::1", "64:ff9b::808:808"):
        assert is_public_address(ipaddress.ip_address(address)), address


def test_dns_rebinding_cannot_switch_address_after_check() -> None:
    answers = iter([[PUBLIC], [ipaddress.ip_address("127.0.0.1")]])
    calls = []

    def resolver(host: str) -> list:
        calls.append(host)
        return next(answers)

    client, recorder = fetcher(ok(), resolver)

    fetch(client, "https://shop.example/p")

    assert calls == ["shop.example"]  # одно разрешение на шаг
    assert [request.url.host for request in recorder.requests] == ["93.184.216.34"]


def test_empty_or_failed_resolution() -> None:
    client, _ = fetcher(ok(), {"shop.example": []})
    assert code(lambda: fetch(client, "https://shop.example/p")) is ErrorCode.INVALID_URL

    def broken(host: str) -> list:
        raise OSError("Name or service not known")

    client, _ = fetcher(ok(), broken)
    assert code(lambda: fetch(client, "https://shop.example/p")) is ErrorCode.TIMEOUT


# --- перенаправления ---


def redirect_to(location: str, status: int = 302) -> Callable:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.headers["Host"] == "shop.example":
            return httpx2.Response(status, headers={"location": location})
        return httpx2.Response(200, content=b"final", headers={"content-type": "text/html"})

    return respond


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_is_followed_with_new_checks(status: int) -> None:
    client, recorder = fetcher(redirect_to("https://www.shop.example/p2", status), {"www.shop.example": [PUBLIC_2]})

    response = fetch(client, "https://shop.example/p")

    assert response.body == b"final"
    assert response.url.url == "https://www.shop.example/p2"
    assert [(r.url.host, r.headers["Host"], r.extensions["sni_hostname"]) for r in recorder.requests] == [
        ("93.184.216.34", "shop.example", "shop.example"),
        ("93.184.216.35", "www.shop.example", "www.shop.example"),
    ]


def test_relative_redirect() -> None:
    client, recorder = fetcher(
        lambda r: httpx2.Response(301, headers={"location": "/new?x=1"}) if r.url.path == "/old" else httpx2.Response(200, content=b"ok")
    )

    assert fetch(client, "https://shop.example/old").url.url == "https://shop.example/new?x=1"
    assert len(recorder.requests) == 2


@pytest.mark.parametrize(
    ("location", "resolve"),
    [
        ("https://internal.example/admin", {"internal.example": [ipaddress.ip_address("192.168.1.1")]}),
        ("http://www.shop.example/p", {}),  # без шифрования
        ("https://127.0.0.1/p", {}),
        ("https://user:pw@www.shop.example/p", {}),
        ("https://www.shop.example:8080/p", {}),
        ("file:///etc/passwd", {}),
    ],
)
def test_redirect_into_forbidden_place_is_refused(location: str, resolve: dict) -> None:
    client, recorder = fetcher(redirect_to(location), resolve)

    assert code(lambda: fetch(client, "https://shop.example/p")) is ErrorCode.INVALID_URL
    assert len(recorder.requests) == 1


def test_redirect_limit() -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        step = int(request.url.path.strip("/") or 0)
        return httpx2.Response(302, headers={"location": f"/{step + 1}"})

    client, recorder = fetcher(respond)

    assert code(lambda: fetch(client, "https://shop.example/0")) is ErrorCode.BLOCKED
    assert len(recorder.requests) == MAX_REDIRECTS + 1


def test_exactly_max_redirects_is_fine() -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        step = int(request.url.path.strip("/"))
        if step == MAX_REDIRECTS:
            return httpx2.Response(200, content=b"done")
        return httpx2.Response(302, headers={"location": f"/{step + 1}"})

    client, _ = fetcher(respond)

    assert fetch(client, "https://shop.example/0").body == b"done"


def test_redirect_without_location_is_returned() -> None:
    client, _ = fetcher(lambda request: httpx2.Response(302))

    assert fetch(client, "https://shop.example/p").status == 302


# --- пределы размера и времени ---


def test_huge_response_is_cut_off() -> None:
    client, _ = fetcher(ok(b"x" * 1001), max_bytes=1000)

    assert code(lambda: fetch(client, "https://shop.example/p")) is ErrorCode.LAYOUT_CHANGED


def test_response_at_size_limit_is_fine() -> None:
    client, _ = fetcher(ok(b"x" * 1000), max_bytes=1000)

    assert len(fetch(client, "https://shop.example/p").body) == 1000


def test_compression_bomb_is_measured_after_unpacking() -> None:
    bomb = gzip.compress(b"\0" * 50_000)
    client, _ = fetcher(
        lambda request: httpx2.Response(200, content=bomb, headers={"content-encoding": "gzip"}), max_bytes=10_000
    )

    assert len(bomb) < 10_000
    assert code(lambda: fetch(client, "https://shop.example/p")) is ErrorCode.LAYOUT_CHANGED


def test_broken_compression() -> None:
    client, _ = fetcher(lambda request: httpx2.Response(200, content=b"not gzip", headers={"content-encoding": "gzip"}))

    assert code(lambda: fetch(client, "https://shop.example/p")) is ErrorCode.LAYOUT_CHANGED


def test_slow_drip_hits_total_deadline() -> None:
    now = [0.0]

    def drip() -> Iterator[bytes]:
        for _ in range(100):
            now[0] += 1.0  # каждый байт — через секунду: отдельные чтения быстрые, всё вместе — нет
            yield b"x"

    client, _ = fetcher(lambda request: httpx2.Response(200, content=drip()), clock=lambda: now[0], total_timeout_s=20.0)

    assert code(lambda: fetch(client, "https://shop.example/p")) is ErrorCode.TIMEOUT


def test_deadline_counts_across_redirects() -> None:
    now = [0.0]

    def respond(request: httpx2.Request) -> httpx2.Response:
        now[0] += 15.0
        return httpx2.Response(302, headers={"location": "/next"}) if request.url.path == "/p" else httpx2.Response(200)

    client, _ = fetcher(respond, clock=lambda: now[0], total_timeout_s=20.0)

    assert code(lambda: fetch(client, "https://shop.example/p")) is ErrorCode.TIMEOUT


@pytest.mark.parametrize(
    "failure",
    [httpx2.ConnectTimeout("t"), httpx2.ReadTimeout("t"), httpx2.ConnectError("refused"), httpx2.RemoteProtocolError("bad")],
)
def test_network_failures_become_timeout(failure: Exception) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        raise failure

    client, _ = fetcher(respond)

    with pytest.raises(PriceWatcherError) as caught:
        fetch(client, "https://shop.example/p")

    assert caught.value.code is ErrorCode.TIMEOUT
    assert caught.value.__cause__ is failure  # причина сохраняется для журнала
