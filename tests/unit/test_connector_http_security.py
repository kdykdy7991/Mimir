from __future__ import annotations

import httpx
import pytest

from src.connectors.http_security import (
    DownloadLimitError, SSRFPolicy, SafeHttpClient, SafeUrlValidator,
    UnsafeUrlError,
)


def resolver(mapping):
    return lambda host, port: mapping.get(host, ())


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.1", "169.254.169.254", "0.0.0.0",
    "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1",
])
def test_blocks_ipv4_ipv6_private_loopback_link_local_and_metadata(address) -> None:
    validator = SafeUrlValidator(
        SSRFPolicy(), resolver=resolver({"evil.example": (address,)}),
    )
    with pytest.raises(UnsafeUrlError):
        validator.validate("https://evil.example/feed")


def test_requires_every_dns_answer_to_be_public() -> None:
    validator = SafeUrlValidator(
        SSRFPolicy(),
        resolver=resolver({"mixed.example": ("93.184.216.34", "10.0.0.2")}),
    )
    with pytest.raises(UnsafeUrlError):
        validator.validate("https://mixed.example/")


@pytest.mark.parametrize("url", [
    "http://public.example/", "file:///etc/passwd",
    "https://user:password@public.example/", "https://public.example:8443/",
])
def test_blocks_scheme_userinfo_and_port_bypasses(url) -> None:
    validator = SafeUrlValidator(
        SSRFPolicy(), resolver=resolver({"public.example": ("93.184.216.34",)}),
    )
    with pytest.raises(UnsafeUrlError):
        validator.validate(url)


def test_redirect_target_is_revalidated_before_second_request() -> None:
    seen = []
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://internal.example/private"})
    client = SafeHttpClient(
        resolver=resolver({
            "public.example": ("93.184.216.34",),
            "internal.example": ("127.0.0.1",),
        }),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(UnsafeUrlError):
        client.get("https://public.example/feed")
    assert seen == ["https://public.example/feed"]


def test_streaming_body_and_declared_length_are_bounded() -> None:
    policy = SSRFPolicy(max_bytes=4)
    public = resolver({"public.example": ("93.184.216.34",)})
    declared = SafeHttpClient(
        policy, resolver=public,
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"content-length": "5"}, content=b"12345",
        )),
    )
    with pytest.raises(DownloadLimitError):
        declared.get("https://public.example/feed")
    streamed = SafeHttpClient(
        policy, resolver=public,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"12345")),
    )
    with pytest.raises(DownloadLimitError):
        streamed.get("https://public.example/feed")


def test_bearer_token_is_never_forwarded_to_cross_origin_redirect() -> None:
    seen = []
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("authorization")))
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "https://cdn.example/feed"})
        return httpx.Response(200, content=b"ok")
    client = SafeHttpClient(
        resolver=resolver({
            "public.example": ("93.184.216.34",),
            "cdn.example": ("93.184.216.35",),
        }),
        transport=httpx.MockTransport(handler), authorization="Bearer secret",
    )
    assert client.get("https://public.example/feed").body == b"ok"
    assert seen == [
        ("https://public.example/feed", "Bearer secret"),
        ("https://cdn.example/feed", None),
    ]
