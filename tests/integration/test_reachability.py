"""Opt-in public-endpoint checks; excluded from the deterministic default suite."""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

import pytest

pytestmark = [
    pytest.mark.reachability,
    pytest.mark.skipif(
        os.environ.get("OPEN_PHARMA_RUN_REACHABILITY") != "1",
        reason="set OPEN_PHARMA_RUN_REACHABILITY=1 to make public-network requests",
    ),
]


@pytest.mark.parametrize(
    ("url", "required_key"),
    [
        (
            "https://clinicaltrials.gov/api/v2/studies?pageSize=1&query.term=aspirin",
            "studies",
        ),
        (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=aspirin&retmode=json&retmax=1",
            "esearchresult",
        ),
        ("https://api.fda.gov/drug/drugsfda.json?limit=1", "results"),
    ],
)
def test_public_api_is_reachable(url: str, required_key: str):
    request = Request(url, headers={"User-Agent": "open-pharma-plugins/reachability-check"})
    with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed HTTPS URLs above
        assert response.status == 200
        payload = json.load(response)
    assert required_key in payload
