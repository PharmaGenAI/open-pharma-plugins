"""Tests for shared.env configuration catalog."""

import os
from pathlib import Path

from shared.env import CONFIG_FIELDS, get_env


def test_config_fields_has_entries():
    assert len(CONFIG_FIELDS) > 0


def test_get_env_returns_default():
    val = get_env("NONEXISTENT_VAR_FOR_TEST", "fallback")
    assert val == "fallback"


def test_config_fields_tuples_valid():
    for entry in CONFIG_FIELDS:
        assert len(entry) == 5
        key, is_secret, group, default, description = entry
        assert isinstance(key, str) and key
        assert isinstance(is_secret, bool)
        assert isinstance(group, str) and group
        assert isinstance(description, str) and description


def test_mutable_data_locations_are_configurable():
    keys = {entry[0] for entry in CONFIG_FIELDS}
    assert {
        "OPEN_PHARMA_HCP_DATA_DIR",
        "OPEN_PHARMA_TRAINING_CONTENT_DIR",
        "OPEN_PHARMA_CAMPAIGN_STORE_DIR",
        "OPEN_PHARMA_NBE_OUTPUT_DIR",
        "OPEN_PHARMA_TA_SCENARIOS_DIR",
        "OPEN_PHARMA_CI_DATA_DIR",
    } <= keys


def test_hcp_synthesis_openrouter_settings_are_in_the_config_catalog():
    fields = {entry[0]: entry for entry in CONFIG_FIELDS}

    assert fields["OPENROUTER_API_KEY"][1:] == (
        True,
        "HCP Intelligence",
        "",
        "OpenRouter API key for optional batch profile synthesis",
    )
    assert fields["OPENROUTER_BASE_URL"][1:] == (
        False,
        "HCP Intelligence",
        "https://openrouter.ai/api/v1",
        "OpenRouter-compatible API base URL for optional batch profile synthesis",
    )


def test_config_writes_are_private_and_atomic(tmp_path, monkeypatch):
    from shared.env import set_config

    config_dir = tmp_path / "config-root"
    monkeypatch.setenv("OPEN_PHARMA_CONFIG_DIR", str(config_dir))
    path = Path(set_config({"SERPER_API_KEY": "secret"}))

    assert path.read_text().endswith("SERPER_API_KEY=secret\n")
    assert not (config_dir / "config.tmp").exists()
    if os.name != "nt":
        assert config_dir.stat().st_mode & 0o777 == 0o700
        assert path.stat().st_mode & 0o777 == 0o600
