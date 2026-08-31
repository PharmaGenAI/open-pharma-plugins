"""Small credential-safe HTTP transport seam for CI providers."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol
from urllib.parse import urlsplit

from shared.filesystem import sanitize_url

_USER_AGENT = "open-pharma-plugins-competitive-intelligence/1"


@dataclass(frozen=True)
class HttpRequest:
    method: Literal["GET", "POST"]
    url: str = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    json_body: Mapping[str, Any] | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class HttpResponse:
    status: int
    url: str = field(repr=False)
    body: bytes = field(repr=False)


class HttpTransport(Protocol):
    def request(self, request: HttpRequest) -> HttpResponse: ...


class TransportError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class UrllibTransport:
    """Production HTTPS adapter that never exposes request credentials in errors."""

    def request(self, request: HttpRequest) -> HttpResponse:
        _validate_request_url(request.url)
        headers = {"Accept": "application/json", "User-Agent": _USER_AGENT, **request.headers}
        body = None
        if request.json_body is not None:
            body = json.dumps(request.json_body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        wire_request = urllib.request.Request(
            request.url,
            data=body,
            headers=headers,
            method=request.method,
        )
        try:
            with urllib.request.urlopen(wire_request, timeout=request.timeout_seconds) as response:
                response_body = response.read()
                response_url = sanitize_url(response.geturl())
                _validate_request_url(response_url)
                return HttpResponse(
                    status=int(getattr(response, "status", response.getcode())),
                    url=response_url,
                    body=response_body,
                )
        except urllib.error.HTTPError:
            raise TransportError("http_error", "provider returned an HTTP error") from None
        except (TimeoutError, socket.timeout):
            raise TransportError("timeout", "provider request timed out") from None
        except (urllib.error.URLError, OSError):
            raise TransportError("network_error", "provider request failed") from None
        except (TypeError, ValueError):
            raise TransportError("invalid_response", "provider returned an invalid response") from None


def _validate_request_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise TransportError("network_error", "provider request URL was rejected")
