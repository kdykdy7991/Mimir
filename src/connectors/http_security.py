"""SSRF-resistant URL validation and bounded HTTP fetching."""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urljoin, urlsplit

import httpx


class UnsafeUrlError(ValueError):
    """A URL or redirect target violates the connector network policy."""


class DownloadLimitError(RuntimeError):
    """A remote body exceeded the configured byte budget."""


Resolver = Callable[[str, int], Iterable[str]]


@dataclass(frozen=True)
class SSRFPolicy:
    allowed_schemes: tuple[str, ...] = ("https",)
    allowed_ports: tuple[int, ...] = (443,)
    max_redirects: int = 5
    max_bytes: int = 10 * 1024 * 1024
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 15.0
    total_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.allowed_schemes or not self.allowed_ports:
            raise ValueError("allowed schemes and ports cannot be empty")
        if self.max_redirects < 0 or self.max_bytes < 1:
            raise ValueError("redirect and download budgets are invalid")
        if min(
            self.connect_timeout_seconds, self.read_timeout_seconds,
            self.total_timeout_seconds,
        ) <= 0:
            raise ValueError("timeouts must be positive")


def system_resolver(host: str, port: int) -> tuple[str, ...]:
    return tuple({
        item[4][0] for item in socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM,
        )
    })


class SafeUrlValidator:
    _BLOCKED_HOSTS = frozenset({
        "localhost", "localhost.localdomain", "metadata.google.internal",
        "metadata", "instance-data", "169.254.169.254.nip.io",
    })

    def __init__(self, policy: SSRFPolicy, *, resolver: Resolver = system_resolver) -> None:
        self.policy = policy
        self._resolver = resolver

    def validate(self, url: str) -> tuple[str, ...]:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise UnsafeUrlError("URL authority is invalid") from exc
        scheme = parsed.scheme.casefold()
        if scheme not in self.policy.allowed_schemes:
            raise UnsafeUrlError("URL scheme is not allowed")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeUrlError("URL userinfo is not allowed")
        host = (parsed.hostname or "").rstrip(".").casefold()
        if not host or host in self._BLOCKED_HOSTS:
            raise UnsafeUrlError("URL hostname is not allowed")
        effective_port = port or (443 if scheme == "https" else 80)
        if effective_port not in self.policy.allowed_ports:
            raise UnsafeUrlError("URL port is not allowed")
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
            addresses = (literal,)
        except ValueError:
            try:
                encoded_host = host.encode("idna").decode("ascii")
                resolved = tuple(self._resolver(encoded_host, effective_port))
            except (OSError, UnicodeError) as exc:
                raise UnsafeUrlError("URL hostname cannot be resolved safely") from exc
            if not resolved:
                raise UnsafeUrlError("URL hostname has no addresses")
            try:
                addresses = tuple(ipaddress.ip_address(value) for value in resolved)
            except ValueError as exc:
                raise UnsafeUrlError("DNS returned an invalid address") from exc
        for address in addresses:
            candidate = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
            if not address.is_global or (candidate is not None and not candidate.is_global):
                raise UnsafeUrlError("URL resolves to a non-public address")
        return tuple(str(address) for address in addresses)


@dataclass(frozen=True)
class BoundedResponse:
    status_code: int
    body: bytes
    headers: dict[str, str]


class SafeHttpClient:
    """No-auto-redirect client that revalidates every redirect hop."""

    def __init__(
        self, policy: SSRFPolicy | None = None, *, resolver: Resolver = system_resolver,
        transport: httpx.BaseTransport | None = None,
        authorization: str | None = None,
    ) -> None:
        self.policy = policy or SSRFPolicy()
        self.validator = SafeUrlValidator(self.policy, resolver=resolver)
        self._authorization = authorization
        timeout = httpx.Timeout(
            self.policy.total_timeout_seconds,
            connect=self.policy.connect_timeout_seconds,
            read=self.policy.read_timeout_seconds,
        )
        self._client = httpx.Client(
            follow_redirects=False, timeout=timeout, transport=transport,
            trust_env=False,
        )

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> BoundedResponse:
        current = url
        initial_origin = self._origin(url)
        deadline = time.monotonic() + self.policy.total_timeout_seconds
        safe_headers = {
            key: value for key, value in (headers or {}).items()
            if key.casefold() in {"accept", "if-none-match", "if-modified-since", "user-agent"}
        }
        for hop in range(self.policy.max_redirects + 1):
            if time.monotonic() >= deadline:
                raise httpx.TimeoutException("connector total timeout exceeded")
            self.validator.validate(current)
            request_headers = dict(safe_headers)
            if self._authorization and self._origin(current) == initial_origin:
                request_headers["authorization"] = self._authorization
            with self._client.stream("GET", current, headers=request_headers) as response:
                if response.has_redirect_location:
                    location = response.headers.get("location")
                    if not location or hop >= self.policy.max_redirects:
                        raise UnsafeUrlError("redirect budget exceeded")
                    current = urljoin(current, location)
                    continue
                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        if int(declared) > self.policy.max_bytes:
                            raise DownloadLimitError("remote body exceeds byte budget")
                    except ValueError as exc:
                        raise DownloadLimitError("invalid remote content length") from exc
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    if time.monotonic() >= deadline:
                        raise httpx.TimeoutException("connector total timeout exceeded")
                    total += len(chunk)
                    if total > self.policy.max_bytes:
                        raise DownloadLimitError("remote body exceeds byte budget")
                    chunks.append(chunk)
                return BoundedResponse(
                    status_code=response.status_code, body=b"".join(chunks),
                    headers={key.casefold(): value for key, value in response.headers.items()},
                )
        raise UnsafeUrlError("redirect budget exceeded")

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int]:
        parsed = urlsplit(url)
        return (
            parsed.scheme.casefold(), (parsed.hostname or "").casefold(),
            parsed.port or (443 if parsed.scheme.casefold() == "https" else 80),
        )

    def close(self) -> None:
        self._client.close()


__all__ = [
    "BoundedResponse", "DownloadLimitError", "SSRFPolicy", "SafeHttpClient",
    "SafeUrlValidator", "UnsafeUrlError", "system_resolver",
]
