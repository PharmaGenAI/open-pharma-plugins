"""Deterministic HTTP fixtures for Competitive Intelligence provider tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from open_pharma_plugins_competitive_intelligence._transport import HttpRequest, HttpResponse


class FixtureTransport:
    """Return queued responses while retaining exact requests for boundary assertions."""

    def __init__(self, responses: Sequence[tuple[str | None, bytes | Exception]]) -> None:
        self._responses = list(responses)
        self.calls: list[HttpRequest] = []

    @classmethod
    def json(cls, payload: Mapping[str, Any], *, url: str) -> "FixtureTransport":
        return cls([(url, json.dumps(payload).encode("utf-8"))])

    @classmethod
    def json_file(cls, path: Path) -> "FixtureTransport":
        return cls([(None, path.read_bytes())])

    @classmethod
    def sequence(cls, *bodies: bytes) -> "FixtureTransport":
        return cls([(None, body) for body in bodies])

    def request(self, request: HttpRequest) -> HttpResponse:
        self.calls.append(request)
        if not self._responses:
            raise AssertionError("unexpected request: no fixture response remains")
        expected_url, body_or_error = self._responses.pop(0)
        if expected_url is not None and request.url != expected_url:
            raise AssertionError(f"unexpected URL: {request.url}")
        if isinstance(body_or_error, Exception):
            raise body_or_error
        return HttpResponse(status=200, url=request.url, body=body_or_error)


@dataclass(frozen=True)
class FixtureFiles:
    root: Path

    def path(self, name: str) -> Path:
        candidate = (self.root / name).resolve()
        if not candidate.is_relative_to(self.root.resolve()) or not candidate.is_file():
            raise AssertionError(f"missing CI fixture: {name}")
        return candidate

    def bytes(self, name: str) -> bytes:
        return self.path(name).read_bytes()


def parse_text_block(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    assert len(blocks) == 1 and blocks[0]["type"] == "text"
    return json.loads(blocks[0]["text"])
