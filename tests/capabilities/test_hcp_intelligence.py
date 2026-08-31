"""Tests for the hcp-intelligence capability."""

import json
import urllib.parse
from pathlib import Path

import open_pharma_plugins_hcp_intelligence as hcp

# ---------------------------------------------------------------------------
# Package sanity
# ---------------------------------------------------------------------------


def test_lists_eleven_tools():
    names = {t["name"] for t in hcp.list_tools()}
    assert names == {
        "get_account",
        "list_accounts",
        "search_clinical_trials",
        "search_congresses",
        "search_grants",
        "search_guidelines",
        "search_hco_web",
        "search_hcp_web",
        "search_orcid",
        "search_publications",
        "update_account",
    }


def test_version():
    assert hcp.__version__ == "1.0.2"


# ---------------------------------------------------------------------------
# All handlers exist and are callable
# ---------------------------------------------------------------------------


def test_list_accounts_handler():
    assert callable(hcp.get_handler("list_accounts"))


def test_get_account_handler():
    assert callable(hcp.get_handler("get_account"))


def test_update_account_handler():
    assert callable(hcp.get_handler("update_account"))


def test_search_publications_handler():
    assert callable(hcp.get_handler("search_publications"))


def test_search_clinical_trials_handler():
    assert callable(hcp.get_handler("search_clinical_trials"))


def test_search_clinical_trials_uses_case_sensitive_api_search_scopes(monkeypatch):
    captured: dict[str, str] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"totalCount": 0, "studies": []}'

    def fake_urlopen(request, **_kwargs):
        captured["url"] = request.full_url
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = hcp.get_handler("search_clinical_trials")(
        {"investigator_name": "Wei Lin Tan", "country": "Singapore", "max_results": 1}
    )

    query = urllib.parse.parse_qs(urllib.parse.urlsplit(captured["url"]).query)
    assert query["query.term"] == ["SEARCH[Study]Wei Lin Tan"]
    assert query["query.locn"] == ["SEARCH[Location]Singapore"]
    assert json.loads(result[0]["text"])["total_count"] == 0


def test_search_grants_handler():
    assert callable(hcp.get_handler("search_grants"))


def test_search_orcid_handler():
    assert callable(hcp.get_handler("search_orcid"))


def test_search_guidelines_handler():
    assert callable(hcp.get_handler("search_guidelines"))


def test_search_congresses_handler():
    assert callable(hcp.get_handler("search_congresses"))


def test_search_hcp_web_handler():
    assert callable(hcp.get_handler("search_hcp_web"))


def test_search_hco_web_handler():
    assert callable(hcp.get_handler("search_hco_web"))


# ---------------------------------------------------------------------------
# Offline tools — list_accounts and get_account
# ---------------------------------------------------------------------------


def test_list_accounts_returns_data():
    result = hcp.get_handler("list_accounts")({})
    data = json.loads(result[0]["text"])
    assert "accounts" in data
    assert isinstance(data["accounts"], list)
    assert len(data["accounts"]) > 0


def test_list_accounts_filter_by_type():
    result = hcp.get_handler("list_accounts")({"account_type": "hcp"})
    data = json.loads(result[0]["text"])
    assert "accounts" in data


def test_get_account_valid():
    listing = json.loads(hcp.get_handler("list_accounts")({})[0]["text"])
    if listing["accounts"]:
        acct_id = listing["accounts"][0]["id"]
        result = hcp.get_handler("get_account")({"account_id": acct_id})
        data = json.loads(result[0]["text"])
        assert "id" in data or "error" not in str(data)


def test_get_account_invalid_id():
    result = hcp.get_handler("get_account")({"account_id": "NONEXISTENT_999"})
    data = json.loads(result[0]["text"])
    assert "error" in data


def test_enrichment_is_written_to_configured_private_data_dir(tmp_path, monkeypatch):
    from open_pharma_plugins_hcp_intelligence._crm_store import write_enrichment

    data_dir = tmp_path / "hcp-data"
    monkeypatch.setenv("OPEN_PHARMA_HCP_DATA_DIR", str(data_dir))
    write_enrichment("HCP001", '{"specialty":"oncology"}', "complete")

    path = data_dir / "enrichment_store.json"
    assert path.is_file()
    assert json.loads(path.read_text())["HCP001"]["status"] == "complete"
    assert Path(path).stat().st_mode & 0o777 == 0o600
