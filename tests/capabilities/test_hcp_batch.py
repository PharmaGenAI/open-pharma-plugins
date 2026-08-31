from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from open_pharma_plugins_hcp_intelligence import _crm_store, batch, batch_cli

HEADERS = "id,name,specialty,country,account_type,institution\n"


def _write_csv(path: Path, rows: str, *, headers: str = HEADERS) -> Path:
    path.write_text(headers + rows, encoding="utf-8")
    return path


def test_load_accounts_from_user_csv_normalizes_supported_rows(tmp_path):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "HCP-001,Alex Tan,Oncology,Singapore,hcp,Example Hospital\nHCO-001,Example Hospital,Oncology,Singapore,hco,\n",
    )

    accounts = batch.load_accounts_from_csv(source)

    assert accounts == [
        {
            "id": "HCP-001",
            "name": "Alex Tan",
            "specialty": "Oncology",
            "country": "Singapore",
            "account_type": "HCP",
            "institution": "Example Hospital",
        },
        {
            "id": "HCO-001",
            "name": "Example Hospital",
            "specialty": "Oncology",
            "country": "Singapore",
            "account_type": "HCO",
            "institution": "",
        },
    ]


@pytest.mark.parametrize("missing_header", batch.INPUT_COLUMNS)
def test_input_preflight_requires_each_contract_header(tmp_path, missing_header):
    headers = [header for header in batch.INPUT_COLUMNS if header != missing_header]
    source = _write_csv(tmp_path / "accounts.csv", "", headers=",".join(headers) + "\n")

    with pytest.raises(ValueError, match=rf"missing columns: .*{missing_header}"):
        batch.load_accounts_from_csv(source)


@pytest.mark.parametrize(
    ("headers", "rows", "message"),
    [
        (
            "id,name,country,account_type\n",
            "HCP-001,Alex Tan,Singapore,HCP\n",
            "missing columns: institution, specialty",
        ),
        (HEADERS, "HCP-001,Alex Tan,Oncology,Singapore,PERSON,Example Hospital\n", "must be HCP or HCO"),
        (
            HEADERS,
            "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n"
            "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
            "row 3: duplicate account id: HCP-001",
        ),
    ],
)
def test_load_accounts_from_user_csv_rejects_invalid_contract(tmp_path, headers, rows, message):
    source = _write_csv(tmp_path / "accounts.csv", rows, headers=headers)

    with pytest.raises(ValueError, match=message):
        batch.load_accounts_from_csv(source)


def test_input_preflight_ignores_extra_columns_and_provider_payloads(tmp_path, monkeypatch):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital,do not share\n",
        headers=HEADERS.rstrip("\n") + ",private_note\n",
    )
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(batch, "_call_tool", lambda name, arguments: calls.append((name, arguments)) or {})
    monkeypatch.setattr(batch.time, "sleep", lambda _seconds: None)

    account = batch.load_accounts_from_csv(source)[0]
    result = batch._enrich_account(account, tmp_path / "output")

    assert account == {
        "id": "HCP-001",
        "name": "Alex Tan",
        "specialty": "Oncology",
        "country": "Singapore",
        "account_type": "HCP",
        "institution": "Example Hospital",
    }
    assert result["account"] == account
    assert calls
    assert all("private_note" not in arguments for _, arguments in calls)
    assert "do not share" not in json.dumps(calls)


def test_public_batch_execution_never_sends_extra_csv_columns_to_providers(tmp_path, monkeypatch):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital,do not share\n",
        headers=HEADERS.rstrip("\n") + ",private_note\n",
    )
    output_dir = tmp_path / "output"
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(batch, "_load_tools", lambda: None)
    monkeypatch.setattr(batch, "_call_tool", lambda name, arguments: calls.append((name, arguments)) or {})
    monkeypatch.setattr(batch.time, "sleep", lambda _seconds: None)

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--concurrency",
            "1",
        ]
    )

    assert exit_code == 0
    assert calls
    assert "do not share" not in json.dumps(calls)
    artifact = json.loads((output_dir / "HCP-001.json").read_text(encoding="utf-8"))
    assert artifact["account"] == batch.load_accounts_from_csv(source)[0]


def test_input_preflight_rejects_control_characters_in_account_id_with_row_number(tmp_path):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "HCP\t001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )

    with pytest.raises(ValueError, match="row 2 account id contains a control character"):
        batch.load_accounts_from_csv(source)


@pytest.mark.parametrize("account_id", ["\tHCP-001", "HCP-001\t"])
def test_input_preflight_rejects_leading_or_trailing_control_in_raw_account_id(tmp_path, account_id):
    source = _write_csv(
        tmp_path / "accounts.csv",
        f"{account_id},Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )

    with pytest.raises(ValueError, match="row 2 account id contains a control character"):
        batch.load_accounts_from_csv(source)


def test_preflight_validates_every_row_before_selection_or_tool_loading(tmp_path, monkeypatch, capsys):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n"
        "HCO-001,Broken Hospital,Oncology,Singapore,INVALID,\n",
    )
    monkeypatch.setattr(batch, "_load_tools", lambda: pytest.fail("network boundary crossed"))

    with pytest.raises(SystemExit) as exc_info:
        batch_cli.main(
            [
                "--input-file",
                str(source),
                "--output-dir",
                str(tmp_path / "output"),
                "--ids",
                "HCP-001",
            ]
        )

    assert exc_info.value.code == 2
    assert "row 3: account_type must be HCP or HCO" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("path_flag", "bad_value", "message"),
    [
        ("--input-file", "accounts\n.csv", "input path contains a control character"),
        ("--output-dir", "output\x7fdir", "output path contains a control character"),
    ],
)
def test_preflight_rejects_path_control_characters(tmp_path, monkeypatch, capsys, path_flag, bad_value, message):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    arguments = [
        "--input-file",
        str(source),
        "--output-dir",
        str(tmp_path / "output"),
    ]
    arguments[arguments.index(path_flag) + 1] = str(tmp_path / bad_value)
    monkeypatch.setattr(batch, "_load_tools", lambda: pytest.fail("network boundary crossed"))

    with pytest.raises(SystemExit) as exc_info:
        batch_cli.main(arguments)

    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err


def test_input_preflight_rejects_non_file_before_tool_loading(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(batch, "_load_tools", lambda: pytest.fail("network boundary crossed"))

    with pytest.raises(SystemExit) as exc_info:
        batch_cli.main(
            [
                "--input-file",
                str(tmp_path),
                "--output-dir",
                str(tmp_path / "output"),
            ]
        )

    assert exc_info.value.code == 2
    assert "input file does not exist" in capsys.readouterr().err


def test_output_directory_preflight_rejects_file_before_tool_loading(tmp_path, monkeypatch, capsys):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_file = tmp_path / "not-a-directory"
    output_file.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(batch, "_load_tools", lambda: pytest.fail("network boundary crossed"))

    with pytest.raises(SystemExit) as exc_info:
        batch_cli.main(["--input-file", str(source), "--output-dir", str(output_file)])

    assert exc_info.value.code == 2
    assert "output path is not a directory" in capsys.readouterr().err
    assert output_file.read_text(encoding="utf-8") == "keep"


def test_output_directory_preflight_rejects_nonempty_reuse_before_tool_loading(tmp_path, monkeypatch, capsys):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    unrelated = output_dir / "operator-notes.txt"
    unrelated.write_bytes(b"operator notes\x00remain unchanged")
    monkeypatch.setattr(batch, "_load_tools", lambda: pytest.fail("network boundary crossed"))

    with pytest.raises(SystemExit) as exc_info:
        batch_cli.main(["--input-file", str(source), "--output-dir", str(output_dir)])

    assert exc_info.value.code == 2
    assert "output directory is not empty; pass --resume to reuse it" in capsys.readouterr().err
    assert unrelated.read_bytes() == b"operator notes\x00remain unchanged"


@pytest.mark.parametrize("precreate_output", [False, True])
def test_output_directory_preflight_accepts_missing_or_empty_directory(tmp_path, monkeypatch, precreate_output):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    if precreate_output:
        output_dir.mkdir()
    monkeypatch.setattr(batch, "_load_tools", lambda: None)
    monkeypatch.setattr(batch, "_enrich_account", lambda account, _output: _raw_result(account))

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--concurrency",
            "1",
        ]
    )

    assert exit_code == 0
    assert (output_dir / "HCP-001.json").is_file()
    assert (output_dir / "batch_manifest.json").is_file()


def test_output_directory_resume_preserves_unrelated_files_byte_for_byte(tmp_path, monkeypatch):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    unrelated = output_dir / "operator-notes.bin"
    original = b"\x00\xffoperator-owned\n"
    unrelated.write_bytes(original)
    monkeypatch.setattr(batch, "_load_tools", lambda: None)
    monkeypatch.setattr(batch, "_enrich_account", lambda account, _output: _raw_result(account))

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--resume",
            "--concurrency",
            "1",
        ]
    )

    assert exit_code == 0
    assert unrelated.read_bytes() == original
    assert (output_dir / "HCP-001.json").is_file()


def test_dry_run_preflight_reports_resolved_plan_without_side_effects(tmp_path, monkeypatch, capsys):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\nHCO-001,Example Hospital,Oncology,Singapore,HCO,\n",
    )
    output_dir = tmp_path / "nested" / "output"
    monkeypatch.setattr(batch, "_load_tools", lambda: pytest.fail("network boundary crossed"))

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--dry-run",
            "--synthesize",
        ]
    )

    stdout = capsys.readouterr().out
    assert exit_code == 0
    assert f"Input: {source.resolve()}" in stdout
    assert f"Output: {output_dir.resolve()}" in stdout
    assert "Selected: 2 total (HCP: 1, HCO: 1)" in stdout
    assert "Provider: https://openrouter.ai/api/v1" in stdout
    assert "Model: deepseek/deepseek-v4-flash-0731" in stdout
    assert "Reasoning effort: high" in stdout
    assert "Timeout: 120s" in stdout
    assert "SDK retries: 0" in stdout
    assert "HCP-001.json" in stdout
    assert "HCO-001.json" in stdout
    assert "batch_summary.csv" in stdout
    assert "batch_manifest.json" in stdout
    assert "No external calls were made." in stdout
    assert "selected account fields and gathered evidence" in stdout
    assert not output_dir.exists()


def test_dry_run_preflight_default_fixture_does_not_create_crm_storage(tmp_path, monkeypatch, capsys):
    crm_dir = tmp_path / "crm-storage"
    monkeypatch.setenv("OPEN_PHARMA_HCP_DATA_DIR", str(crm_dir))
    monkeypatch.setattr("shared.env._config_cache", {})
    monkeypatch.setattr(batch, "_load_tools", lambda: pytest.fail("network boundary crossed"))

    exit_code = batch_cli.main(["--output-dir", str(tmp_path / "output"), "--dry-run"])

    stdout = capsys.readouterr().out
    assert exit_code == 0
    assert f"Input: {batch.BUNDLED_INPUT.resolve()}" in stdout
    assert "HCP-AU-001" in stdout
    assert not crm_dir.exists()


def test_preflight_valid_selection_with_no_matches_exits_zero_without_side_effects(tmp_path, monkeypatch, capsys):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    monkeypatch.setattr(batch, "_load_tools", lambda: pytest.fail("network boundary crossed"))

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--ids",
            "NOT-PRESENT",
        ]
    )

    assert exit_code == 0
    assert "No accounts match the filters." in capsys.readouterr().out
    assert not output_dir.exists()


def test_zero_match_selection_ignores_nonempty_output_directory(tmp_path, monkeypatch, capsys):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    unrelated = output_dir / "operator-notes.txt"
    unrelated.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(batch, "_load_tools", lambda: pytest.fail("network boundary crossed"))

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--ids",
            "NOT-PRESENT",
        ]
    )

    assert exit_code == 0
    assert "No accounts match the filters." in capsys.readouterr().out
    assert unrelated.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("collision_name", ["HCP-001.json", "batch_summary.csv", "batch_manifest.json"])
def test_preflight_rejects_exact_input_output_artifact_collisions(tmp_path, monkeypatch, capsys, collision_name):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    source = _write_csv(
        output_dir / collision_name,
        "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    original = source.read_bytes()
    monkeypatch.setattr(batch, "_load_tools", lambda: pytest.fail("network boundary crossed"))

    with pytest.raises(SystemExit) as exc_info:
        batch_cli.main(
            [
                "--input-file",
                str(source),
                "--output-dir",
                str(output_dir),
                "--resume",
            ]
        )

    assert exc_info.value.code == 2
    assert "input file collides with planned output" in capsys.readouterr().err
    assert source.read_bytes() == original


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--concurrency", "0"], "--concurrency must be at least 1"),
        (
            ["--synthesis-timeout-seconds", "0"],
            "--synthesis-timeout-seconds must be greater than 0",
        ),
        (["--account-type", "PERSON"], "invalid choice: 'PERSON'"),
    ],
)
def test_preflight_maps_invalid_options_to_argparse_exit_two(tmp_path, monkeypatch, capsys, arguments, message):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    monkeypatch.setattr(batch, "_load_tools", lambda: pytest.fail("network boundary crossed"))

    with pytest.raises(SystemExit) as exc_info:
        batch_cli.main(["--input-file", str(source), "--output-dir", str(tmp_path / "output"), *arguments])

    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err


def test_validate_synthesized_profile_uses_capability_schema():
    valid = {
        "full_name": "Alex Tan",
        "specialty": "Oncology",
        "country": "Singapore",
        "profile_completeness": 0.0,
        "sources_consulted": [],
        "built_at": "2026-08-27T00:00:00Z",
    }

    assert batch.validate_synthesized_profile(json.dumps(valid), "HCP") == valid

    invalid = {**valid, "profile_completeness": 2.0}
    with pytest.raises(ValueError, match="profile_completeness"):
        batch.validate_synthesized_profile(json.dumps(invalid), "HCP")


def test_write_batch_json_is_private(tmp_path):
    output = batch.write_batch_json(tmp_path / "private" / "result.json", {"ok": True})

    assert json.loads(output.read_text()) == {"ok": True}
    if os.name != "nt":
        assert output.parent.stat().st_mode & 0o777 == 0o700
        assert output.stat().st_mode & 0o777 == 0o600


def test_build_batch_manifest_records_input_provenance_without_names(tmp_path):
    source = _write_csv(tmp_path / "accounts.csv", "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n")
    summary_csv = {
        "status": "completed",
        "path": str((tmp_path / "output" / "batch_summary.csv").resolve()),
        "schema_version": 1,
        "row_count": 1,
        "sha256": "summary-sha256",
    }

    manifest = batch.build_batch_manifest(
        input_path=source,
        input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        selected_count=1,
        synthesis=True,
        model="test-model",
        base_url="https://openrouter.example.test/api/v1",
        reasoning_effort="xhigh",
        synthesis_timeout_seconds=45.0,
        concurrency=2,
        results=[{"account_id": "HCP-001", "status": "completed", "tools_failed": []}],
        summary_csv=summary_csv,
        started_at="2026-08-27T00:00:00Z",
        completed_at="2026-08-27T00:01:00Z",
    )

    assert manifest["schema_version"] == 2
    assert manifest["input"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["summary"] == {"selected": 1, "completed": 1, "partial": 0, "failed": 0, "skipped": 0}
    assert manifest["accounts"] == [{"account_id": "HCP-001", "status": "completed", "tools_failed": []}]
    assert manifest["processing"] == {
        "synthesis": True,
        "model": "test-model",
        "base_url": "https://openrouter.example.test/api/v1",
        "reasoning_effort": "xhigh",
        "timeout_seconds": 45.0,
        "concurrency": 2,
    }
    assert manifest["outputs"] == {"summary_csv": summary_csv}
    assert "Alex Tan" not in json.dumps(manifest)


def test_plan_hashes_and_parses_one_immutable_input_snapshot(tmp_path, monkeypatch):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "HCP-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    original = source.read_bytes()
    reads = 0
    original_read_bytes = Path.read_bytes

    def counted_read_bytes(path: Path) -> bytes:
        nonlocal reads
        if path.resolve() == source.resolve():
            reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
    options = batch.BatchOptions(
        input_file=source,
        output_dir=tmp_path / "output",
        country=None,
        account_type=None,
        ids=(),
        concurrency=1,
        resume=False,
        write_back=False,
        synthesize=False,
        base_url=batch.DEFAULT_OPENROUTER_BASE_URL,
        api_key_env="OPENROUTER_API_KEY",
        model=batch.DEFAULT_SYNTHESIS_MODEL,
        reasoning_effort="high",
        synthesis_timeout_seconds=120.0,
    )

    plan = batch.plan_batch(options)
    source.write_text(
        HEADERS + "HCP-999,Mallory,Neurology,Canada,HCP,Other Hospital\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(batch, "_load_tools", lambda: None)
    monkeypatch.setattr(batch, "_enrich_account", lambda account, _output: _raw_result(account))
    outcome = batch.run_batch(plan)

    assert reads == 1
    assert plan.accounts[0]["id"] == "HCP-001"
    assert plan.input_sha256 == hashlib.sha256(original).hexdigest()
    assert outcome.manifest["input"]["sha256"] == hashlib.sha256(original).hexdigest()


def test_cli_dry_run_accepts_user_input_file(tmp_path):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    result = subprocess.run(
        [
            "uv",
            "run",
            "open-pharma-plugins-hcp-batch",
            "--input-file",
            str(source),
            "--output-dir",
            str(tmp_path / "output"),
            "--dry-run",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Would process 1 account(s)" in result.stdout
    assert "CUSTOM-001" in result.stdout
    assert "HCP-AU-001" not in result.stdout
    assert "Synthesis: disabled" in result.stdout
    assert "Provider: https://openrouter.ai/api/v1" in result.stdout
    assert "Model: deepseek/deepseek-v4-flash-0731" in result.stdout
    assert "Reasoning effort: high" in result.stdout
    assert "Timeout: 120s" in result.stdout
    assert "SDK retries: 0" in result.stdout
    assert not (tmp_path / "output").exists()


def test_cli_synthesis_defaults_to_pinned_deepseek_v4_flash_on_openrouter(tmp_path):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    result = subprocess.run(
        [
            "uv",
            "run",
            "open-pharma-plugins-hcp-batch",
            "--input-file",
            str(source),
            "--dry-run",
            "--synthesize",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Synthesis: deepseek/deepseek-v4-flash-0731 via https://openrouter.ai/api/v1" in result.stdout
    assert "Reasoning effort: high; timeout: 120s; SDK retries: 0" in result.stdout


def test_cli_synthesis_accepts_xhigh_reasoning_and_custom_timeout(tmp_path):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    result = subprocess.run(
        [
            "uv",
            "run",
            "open-pharma-plugins-hcp-batch",
            "--input-file",
            str(source),
            "--dry-run",
            "--synthesize",
            "--reasoning-effort",
            "xhigh",
            "--synthesis-timeout-seconds",
            "45",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Reasoning effort: xhigh; timeout: 45s; SDK retries: 0" in result.stdout


def test_cli_rejects_non_positive_synthesis_timeout(tmp_path):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    result = subprocess.run(
        [
            "uv",
            "run",
            "open-pharma-plugins-hcp-batch",
            "--input-file",
            str(source),
            "--dry-run",
            "--synthesize",
            "--synthesis-timeout-seconds",
            "0",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--synthesis-timeout-seconds must be greater than 0" in result.stderr


@pytest.mark.parametrize("timeout", ["nan", "inf", "-inf"])
def test_cli_rejects_non_finite_synthesis_timeout(tmp_path, timeout):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    result = subprocess.run(
        [
            "uv",
            "run",
            "open-pharma-plugins-hcp-batch",
            "--input-file",
            str(source),
            "--dry-run",
            "--synthesize",
            f"--synthesis-timeout-seconds={timeout}",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--synthesis-timeout-seconds must be greater than 0 and finite" in result.stderr


def test_cli_synthesis_uses_configured_openrouter_base_url(tmp_path):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    config = tmp_path / "config"
    config.write_text("OPENROUTER_BASE_URL=https://openrouter.example.test/api/v1\n", encoding="utf-8")
    env = {**os.environ, "OPEN_PHARMA_CONFIG": str(config)}
    env.pop("OPENROUTER_BASE_URL", None)
    result = subprocess.run(
        [
            "uv",
            "run",
            "open-pharma-plugins-hcp-batch",
            "--input-file",
            str(source),
            "--dry-run",
            "--synthesize",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Synthesis: deepseek/deepseek-v4-flash-0731 via https://openrouter.example.test/api/v1" in result.stdout


def test_non_retryable_http_client_error_is_not_retried(monkeypatch):
    calls = 0

    class FailingTool:
        @staticmethod
        def handle(_arguments):
            nonlocal calls
            calls += 1
            raise urllib.error.HTTPError("https://example.test", 400, "bad request", {}, None)

    monkeypatch.setitem(batch._TOOL_MODULES, "failing", FailingTool)
    monkeypatch.setattr(batch.time, "sleep", lambda _seconds: None)

    assert batch._call_tool("failing", {}) is None
    assert calls == 1


def _raw_result(account: dict, *, tools_failed: list[str] | None = None) -> dict:
    return {
        "account": account,
        "search_results": {"search_orcid": {"results": []}},
        "tools_called": ["search_orcid"],
        "tools_succeeded": [] if tools_failed else ["search_orcid"],
        "tools_failed": tools_failed or [],
    }


def _valid_hcp_profile(name: str = "Alex Tan") -> dict:
    return {
        "full_name": name,
        "specialty": "Oncology",
        "country": "Singapore",
        "profile_completeness": 0.0,
        "sources_consulted": [],
        "built_at": "2026-08-27T00:00:00Z",
    }


def _install_provider_fakes(monkeypatch, *, synthesis_by_name: dict[str, object] | None = None) -> None:
    class FakeTool:
        def __init__(self, tool_name: str):
            self.tool_name = tool_name

        def handle(self, arguments):
            name = next(
                (arguments[key] for key in ("name", "author_name", "investigator_name", "pi_name") if key in arguments),
                "",
            )
            if name == "Partial Account" and self.tool_name == "search_orcid":
                raise urllib.error.HTTPError("https://example.test", 400, "provider rejected", {}, None)
            return [{"type": "text", "text": json.dumps({"results": []})}]

    monkeypatch.setattr(batch, "_TOOL_MODULES", {name: FakeTool(name) for name in batch.HCP_TOOLS})
    if synthesis_by_name is None:
        return

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            account_name = next(name for name in synthesis_by_name if name in kwargs["messages"][1]["content"])
            outcome = synthesis_by_name[account_name]
            if isinstance(outcome, Exception):
                raise outcome
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(outcome)))])

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))


def test_synthesis_sends_reasoning_and_bounded_retry_contract_to_openrouter(monkeypatch):
    captured: dict[str, dict] = {}
    response_content = '{"full_name":"Alex Tan"}'

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=response_content))])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    monkeypatch.setattr("shared.env._config_cache", {})
    args = SimpleNamespace(
        api_key_env="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        model="deepseek/deepseek-v4-flash-0731",
        reasoning_effort="high",
        synthesis_timeout_seconds=120.0,
    )
    raw = {
        "account": {"id": "CUSTOM-001", "name": "Alex Tan", "account_type": "HCP"},
        "search_results": {"search_orcid": {"results": []}},
    }

    assert batch._synthesize(raw, args) == response_content
    assert captured["client"] == {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "test-secret",
        "timeout": 120.0,
        "max_retries": 0,
    }
    assert captured["request"]["model"] == "deepseek/deepseek-v4-flash-0731"
    assert captured["request"]["extra_body"] == {
        "reasoning": {"effort": "high"},
        "provider": {"require_parameters": True},
    }
    response_format = captured["request"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "hcp_profile"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["properties"]["full_name"]["type"] == "string"
    assert schema["$defs"]["SourceType"]["enum"] == [
        "pubmed",
        "clinical_trials",
        "web",
        "registry",
    ]


def test_synthesis_reports_empty_completion_metadata(monkeypatch):
    class FakeOpenAI:
        def __init__(self, **_kwargs):
            response = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="length",
                        message=SimpleNamespace(content=None),
                    )
                ],
                usage=SimpleNamespace(
                    completion_tokens=8192,
                    completion_tokens_details=SimpleNamespace(reasoning_tokens=8192),
                ),
            )
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: response))

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret")
    monkeypatch.setattr("shared.env._config_cache", {})
    args = SimpleNamespace(
        api_key_env="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        model="deepseek/deepseek-v4-flash-0731",
        reasoning_effort="high",
        synthesis_timeout_seconds=120.0,
    )
    raw = {
        "account": {"id": "CUSTOM-001", "name": "Alex Tan", "account_type": "HCP"},
        "search_results": {},
    }

    with pytest.raises(
        ValueError,
        match=(
            r"OpenRouter returned no final content \(finish_reason=length, "
            r"completion_tokens=8192, reasoning_tokens=8192\)"
        ),
    ):
        batch._synthesize(raw, args)


def test_cli_synthesis_requires_openrouter_key_before_model_call(tmp_path, monkeypatch, capsys):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("shared.env._config_cache", {})
    monkeypatch.setattr(batch, "_load_tools", lambda: None)
    monkeypatch.setattr(batch, "_enrich_account", lambda account, _output: _raw_result(account))
    assert (
        batch_cli.main(
            [
                "--input-file",
                str(source),
                "--output-dir",
                str(output_dir),
                "--concurrency",
                "1",
                "--synthesize",
            ]
        )
        == 1
    )
    stderr = capsys.readouterr().err
    assert "OPENROUTER_API_KEY not set" in stderr
    assert "OPENAI_API_KEY" not in stderr


def test_custom_batch_writes_artifacts_and_manifest_without_demo_writeback(tmp_path, monkeypatch):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    writebacks: list[tuple] = []
    monkeypatch.setattr(batch, "_load_tools", lambda: None)
    monkeypatch.setattr(batch, "_enrich_account", lambda account, _output: _raw_result(account))
    monkeypatch.setattr(_crm_store, "write_enrichment", lambda *args: writebacks.append(args))
    assert (
        batch_cli.main(
            [
                "--input-file",
                str(source),
                "--output-dir",
                str(output_dir),
                "--concurrency",
                "1",
            ]
        )
        == 0
    )
    assert writebacks == []
    result = json.loads((output_dir / "CUSTOM-001.json").read_text())
    assert result["account"]["id"] == "CUSTOM-001"
    manifest = json.loads((output_dir / "batch_manifest.json").read_text())
    assert manifest["summary"] == {
        "selected": 1,
        "completed": 1,
        "partial": 0,
        "failed": 0,
        "skipped": 0,
    }
    assert manifest["accounts"][0]["profile_validated"] is False


def test_custom_batch_writeback_requires_explicit_flag(tmp_path, monkeypatch):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    writebacks: list[tuple] = []
    monkeypatch.setattr(batch, "_load_tools", lambda: None)
    monkeypatch.setattr(batch, "_enrich_account", lambda account, _output: _raw_result(account))
    monkeypatch.setattr(_crm_store, "write_enrichment", lambda *args: writebacks.append(args))
    assert (
        batch_cli.main(
            [
                "--input-file",
                str(source),
                "--output-dir",
                str(tmp_path / "output"),
                "--concurrency",
                "1",
                "--write-back",
            ]
        )
        == 0
    )
    assert len(writebacks) == 1
    assert writebacks[0][0] == "CUSTOM-001"


@pytest.mark.parametrize("concurrency", [1, 2])
def test_writeback_failure_is_not_retried_and_later_accounts_complete(tmp_path, monkeypatch, concurrency):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "FIRST-001,First Account,Oncology,Singapore,HCP,Example Hospital\n"
        "SECOND-001,Second Account,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    writebacks: list[str] = []
    monkeypatch.setattr(batch, "_load_tools", lambda: None)
    monkeypatch.setattr(batch, "_enrich_account", lambda account, _output: _raw_result(account))

    def write_enrichment(account_id, _payload, _status):
        writebacks.append(account_id)
        if account_id == "FIRST-001":
            raise RuntimeError("write-back unavailable")

    monkeypatch.setattr(_crm_store, "write_enrichment", write_enrichment)

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--write-back",
            "--concurrency",
            str(concurrency),
        ]
    )

    assert exit_code == 1
    assert sorted(writebacks) == ["FIRST-001", "SECOND-001"]
    manifest = json.loads((output_dir / "batch_manifest.json").read_text(encoding="utf-8"))
    assert [entry["account_id"] for entry in manifest["accounts"]] == ["FIRST-001", "SECOND-001"]
    assert [entry["status"] for entry in manifest["accounts"]] == ["failed", "completed"]
    rows = list(csv.DictReader((output_dir / "batch_summary.csv").read_text(encoding="utf-8-sig").splitlines()))
    assert [row["account_id"] for row in rows] == ["FIRST-001", "SECOND-001"]
    assert [row["status"] for row in rows] == ["failed", "completed"]


def test_synthesis_provider_failure_persists_sanitized_class_and_context(tmp_path, monkeypatch, capsys):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    credential = "credential-must-not-leak"
    monkeypatch.setenv("CUSTOM_SYNTHESIS_API_KEY", credential)
    monkeypatch.setattr("shared.env._config_cache", {})
    monkeypatch.setattr(batch, "_load_tools", lambda: None)
    monkeypatch.setattr(batch, "_enrich_account", lambda account, _output: _raw_result(account))

    class FailingCompletions:
        @staticmethod
        def create(**_kwargs):
            raise TimeoutError(f"provider timed out with {credential}")

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FailingCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--synthesize",
            "--api-key-env",
            "CUSTOM_SYNTHESIS_API_KEY",
            "--concurrency",
            "1",
        ]
    )

    assert exit_code == 1
    artifact = json.loads((output_dir / "CUSTOM-001.json").read_text(encoding="utf-8"))
    assert "synthesis provider failed (TimeoutError)" in artifact["synthesis_error"]
    assert "provider timed out with [REDACTED]" in artifact["synthesis_error"]
    persisted = b"\n".join(path.read_bytes() for path in output_dir.iterdir() if path.is_file())
    captured = capsys.readouterr()
    assert credential.encode() not in persisted
    assert credential not in captured.out
    assert credential not in captured.err


def test_resume_reuses_only_a_canonical_account_bound_artifact(tmp_path, monkeypatch):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    account = batch.load_accounts_from_csv(source)[0]
    batch.write_batch_json(output_dir / "CUSTOM-001.json", _raw_result(account))
    monkeypatch.setattr(batch, "_load_tools", lambda: None)
    monkeypatch.setattr(
        batch,
        "_enrich_account",
        lambda *_args: pytest.fail("resume must not reprocess an existing artifact"),
    )
    assert (
        batch_cli.main(
            [
                "--input-file",
                str(source),
                "--output-dir",
                str(output_dir),
                "--resume",
            ]
        )
        == 0
    )
    manifest = json.loads((output_dir / "batch_manifest.json").read_text())
    assert manifest["summary"]["skipped"] == 1
    assert manifest["accounts"] == [
        {
            "account_id": "CUSTOM-001",
            "status": "skipped",
            "tools_failed": [],
            "output_file": str((output_dir / "CUSTOM-001.json").resolve()),
            "profile_validated": False,
        }
    ]


@pytest.mark.parametrize("include_profile", [False, True])
def test_resume_rejects_foreign_account_artifact_even_with_schema_valid_profile(tmp_path, monkeypatch, include_profile):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    foreign = {
        "id": "FOREIGN-001",
        "name": "Foreign Person",
        "specialty": "Oncology",
        "country": "Singapore",
        "account_type": "HCP",
        "institution": "Example Hospital",
    }
    artifact = _raw_result(foreign)
    if include_profile:
        artifact["synthesized_profile"] = _valid_hcp_profile("Foreign Person")
    batch.write_batch_json(output_dir / "CUSTOM-001.json", artifact)
    researched: list[str] = []
    monkeypatch.setattr(batch, "_load_tools", lambda: None)
    monkeypatch.setattr(
        batch,
        "_enrich_account",
        lambda account, _output: researched.append(account["id"]) or _raw_result(account),
    )
    if include_profile:
        monkeypatch.setattr(batch, "_synthesize", lambda *_args: json.dumps(_valid_hcp_profile()))

    arguments = [
        "--input-file",
        str(source),
        "--output-dir",
        str(output_dir),
        "--resume",
        "--concurrency",
        "1",
    ]
    if include_profile:
        arguments.append("--synthesize")
    exit_code = batch_cli.main(arguments)

    assert exit_code == 0
    assert researched == ["CUSTOM-001"]
    repaired = json.loads((output_dir / "CUSTOM-001.json").read_text(encoding="utf-8"))
    assert repaired["account"] == batch.load_accounts_from_csv(source)[0]


def test_resume_rejects_symlinked_artifact_before_synthesis(tmp_path, monkeypatch):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    artifact_path = output_dir / "CUSTOM-001.json"
    account = batch.load_accounts_from_csv(source)[0]
    target = tmp_path / "foreign.json"
    batch.write_batch_json(
        target,
        {**_raw_result(account), "synthesized_profile": _valid_hcp_profile()},
    )
    artifact_path.symlink_to(target)
    researched: list[str] = []
    synthesized_accounts: list[dict] = []
    monkeypatch.setattr(batch, "_load_tools", lambda: None)
    monkeypatch.setattr(
        batch,
        "_enrich_account",
        lambda selected, _output: researched.append(selected["id"]) or _raw_result(selected),
    )

    def synthesize(raw, _options):
        synthesized_accounts.append(raw["account"])
        return json.dumps(_valid_hcp_profile())

    monkeypatch.setattr(batch, "_synthesize", synthesize)

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--resume",
            "--synthesize",
            "--concurrency",
            "1",
        ]
    )

    assert exit_code == 0
    assert researched == ["CUSTOM-001"]
    assert synthesized_accounts == [account]
    assert artifact_path.is_file() and not artifact_path.is_symlink()


def test_resume_rejects_non_regular_artifact_without_synthesizing_it(tmp_path, monkeypatch):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    artifact_path = output_dir / "CUSTOM-001.json"
    artifact_path.mkdir()
    account = batch.load_accounts_from_csv(source)[0]
    monkeypatch.setattr(batch, "_load_tools", lambda: None)
    monkeypatch.setattr(batch, "_enrich_account", lambda selected, _output: _raw_result(selected))
    monkeypatch.setattr(batch, "_synthesize", lambda *_args: pytest.fail("non-regular evidence reached synthesis"))

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--resume",
            "--synthesize",
            "--concurrency",
            "1",
        ]
    )

    assert exit_code == 1
    assert artifact_path.is_dir()
    manifest = json.loads((output_dir / "batch_manifest.json").read_text(encoding="utf-8"))
    assert manifest["summary"]["failed"] == 1
    assert manifest["accounts"][0]["account_id"] == account["id"]


def test_resume_synthesis_reuses_existing_raw_evidence_without_research(tmp_path, monkeypatch):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    account = batch.load_accounts_from_csv(source)[0]
    batch.write_batch_json(output_dir / "CUSTOM-001.json", _raw_result(account))
    monkeypatch.setattr(batch, "_load_tools", lambda: None)
    monkeypatch.setattr(
        batch,
        "_enrich_account",
        lambda *_args: pytest.fail("synthesis resume must reuse raw evidence"),
    )
    monkeypatch.setattr(
        batch,
        "_synthesize",
        lambda *_args: json.dumps(
            {
                "full_name": "Alex Tan",
                "specialty": "Oncology",
                "country": "Singapore",
                "profile_completeness": 0.0,
                "sources_consulted": [],
                "built_at": "2026-08-27T00:00:00Z",
            }
        ),
    )
    assert (
        batch_cli.main(
            [
                "--input-file",
                str(source),
                "--output-dir",
                str(output_dir),
                "--resume",
                "--synthesize",
                "--concurrency",
                "1",
            ]
        )
        == 0
    )
    result = json.loads((output_dir / "CUSTOM-001.json").read_text())
    assert result["synthesized_profile"]["full_name"] == "Alex Tan"
    manifest = json.loads((output_dir / "batch_manifest.json").read_text())
    assert manifest["summary"]["completed"] == 1
    assert manifest["accounts"][0]["profile_validated"] is True
    assert manifest["processing"] == {
        "synthesis": True,
        "model": "deepseek/deepseek-v4-flash-0731",
        "base_url": "https://openrouter.ai/api/v1",
        "reasoning_effort": "high",
        "timeout_seconds": 120.0,
        "concurrency": 1,
    }


def test_invalid_synthesized_profile_fails_account_and_preserves_raw_evidence(tmp_path, monkeypatch):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    monkeypatch.setattr(batch, "_load_tools", lambda: None)
    monkeypatch.setattr(batch, "_enrich_account", lambda account, _output: _raw_result(account))
    monkeypatch.setattr(
        batch,
        "_synthesize",
        lambda *_args: json.dumps(
            {
                "full_name": "Alex Tan",
                "specialty": "Oncology",
                "country": "Singapore",
                "profile_completeness": 2.0,
                "sources_consulted": [],
                "built_at": "2026-08-27T00:00:00Z",
            }
        ),
    )
    assert (
        batch_cli.main(
            [
                "--input-file",
                str(source),
                "--output-dir",
                str(output_dir),
                "--concurrency",
                "1",
                "--synthesize",
            ]
        )
        == 1
    )
    result = json.loads((output_dir / "CUSTOM-001.json").read_text())
    assert result["search_results"] == {"search_orcid": {"results": []}}
    assert "synthesized_profile" not in result
    assert "profile_completeness" in result["synthesis_error"]
    manifest = json.loads((output_dir / "batch_manifest.json").read_text())
    assert manifest["summary"]["failed"] == 1


def test_executed_batch_exports_every_status_and_manifest_v2_metadata(tmp_path, monkeypatch, capsys):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "COMPLETED-001,Completed Account,Oncology,Singapore,HCP,Example Hospital\n"
        "PARTIAL-001,Partial Account,Oncology,Singapore,HCP,Example Hospital\n"
        "FAILED-001,Failed Account,Oncology,Singapore,HCP,Example Hospital\n"
        "SKIPPED-001,Skipped Account,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    skipped_account = batch.load_accounts_from_csv(source)[3]
    batch.write_batch_json(
        output_dir / "SKIPPED-001.json",
        {**_raw_result(skipped_account), "synthesized_profile": _valid_hcp_profile("Skipped Account")},
    )
    _install_provider_fakes(
        monkeypatch,
        synthesis_by_name={
            "Completed Account": _valid_hcp_profile("Completed Account"),
            "Partial Account": _valid_hcp_profile("Partial Account"),
            "Failed Account": RuntimeError("provider synthesis failed"),
        },
    )

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--resume",
            "--synthesize",
            "--api-key-env",
            "NONE",
            "--concurrency",
            "1",
        ]
    )

    summary_path = output_dir / "batch_summary.csv"
    manifest_path = output_dir / "batch_manifest.json"
    rows = list(csv.DictReader(summary_path.read_text(encoding="utf-8-sig").splitlines()))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stdout = capsys.readouterr().out
    assert exit_code == 1
    assert [row["account_id"] for row in rows] == [
        "COMPLETED-001",
        "PARTIAL-001",
        "FAILED-001",
        "SKIPPED-001",
    ]
    assert [row["status"] for row in rows] == ["completed", "partial", "failed", "skipped"]
    assert manifest["schema_version"] == 2
    assert manifest["summary"] == {
        "selected": 4,
        "completed": 1,
        "partial": 1,
        "failed": 1,
        "skipped": 1,
    }
    assert manifest["outputs"]["summary_csv"] == {
        "status": "completed",
        "path": str(summary_path.resolve()),
        "schema_version": 1,
        "row_count": 4,
        "sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
    }
    assert "completed=1  partial=1  failed=1  skipped=1" in stdout
    assert f"Summary CSV: {summary_path.resolve()}" in stdout
    assert f"Manifest: {manifest_path.resolve()}" in stdout
    assert f"Output directory: {output_dir.resolve()}" in stdout


def test_raw_batch_with_completed_and_skipped_accounts_exits_zero_and_rebuilds_outputs(tmp_path, monkeypatch):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "COMPLETED-001,Completed Account,Oncology,Singapore,HCP,Example Hospital\n"
        "SKIPPED-001,Skipped Account,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    skipped_account = batch.load_accounts_from_csv(source)[1]
    batch.write_batch_json(output_dir / "SKIPPED-001.json", _raw_result(skipped_account))
    _install_provider_fakes(monkeypatch)

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--resume",
            "--concurrency",
            "1",
        ]
    )

    rows = list(csv.DictReader((output_dir / "batch_summary.csv").read_text(encoding="utf-8-sig").splitlines()))
    manifest = json.loads((output_dir / "batch_manifest.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert [row["status"] for row in rows] == ["completed", "skipped"]
    assert manifest["summary"]["completed"] == 1
    assert manifest["summary"]["skipped"] == 1


def test_real_csv_filesystem_failure_preserves_json_writes_failed_manifest_and_exits_one(tmp_path, monkeypatch, capsys):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    summary_path = output_dir / "batch_summary.csv"
    summary_path.mkdir()
    _install_provider_fakes(monkeypatch)

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--resume",
            "--concurrency",
            "1",
        ]
    )

    manifest_path = output_dir / "batch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_metadata = manifest["outputs"]["summary_csv"]
    captured = capsys.readouterr()
    expected_error = (
        "CSV export failed (PermissionError)" if os.name == "nt" else "CSV export failed (IsADirectoryError)"
    )
    assert exit_code == 1
    assert (output_dir / "CUSTOM-001.json").is_file()
    assert summary_path.is_dir()
    assert list(output_dir.glob(".batch_summary.csv.*")) == []
    assert output_metadata == {
        "status": "failed",
        "path": str(summary_path.resolve()),
        "schema_version": 1,
        "error": expected_error,
    }
    assert "row_count" not in output_metadata
    assert "sha256" not in output_metadata
    assert expected_error in captured.err
    assert f"Manifest: {manifest_path.resolve()}" in captured.out


@pytest.mark.parametrize("concurrency", [1, 2])
def test_resume_malformed_artifact_is_researched_normally_in_input_order(tmp_path, monkeypatch, concurrency):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "MALFORMED-001,Malformed Account,Oncology,Singapore,HCP,Example Hospital\n"
        "FRESH-001,Fresh Account,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    malformed_path = output_dir / "MALFORMED-001.json"
    malformed_path.write_bytes(b'{"truncated":')
    _install_provider_fakes(monkeypatch)

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--resume",
            "--concurrency",
            str(concurrency),
        ]
    )

    rows = list(csv.DictReader((output_dir / "batch_summary.csv").read_text(encoding="utf-8-sig").splitlines()))
    manifest = json.loads((output_dir / "batch_manifest.json").read_text(encoding="utf-8"))
    repaired = json.loads(malformed_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert [row["account_id"] for row in rows] == ["MALFORMED-001", "FRESH-001"]
    assert [row["status"] for row in rows] == ["completed", "completed"]
    assert [entry["account_id"] for entry in manifest["accounts"]] == ["MALFORMED-001", "FRESH-001"]
    assert manifest["summary"] == {
        "selected": 2,
        "completed": 2,
        "partial": 0,
        "failed": 0,
        "skipped": 0,
    }
    assert repaired["account"]["id"] == "MALFORMED-001"


def test_resume_skips_only_schema_valid_synthesized_profile_and_rebuilds_summary(tmp_path, monkeypatch):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    account = batch.load_accounts_from_csv(source)[0]
    artifact_path = batch.write_batch_json(
        output_dir / "CUSTOM-001.json",
        {**_raw_result(account), "synthesized_profile": _valid_hcp_profile()},
    )
    artifact_before = artifact_path.read_bytes()

    class UnexpectedProvider:
        @staticmethod
        def handle(_arguments):
            pytest.fail("valid resume must not research")

    monkeypatch.setattr(batch, "_TOOL_MODULES", {name: UnexpectedProvider for name in batch.HCP_TOOLS})
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda **_kwargs: pytest.fail("no synthesis")))

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--resume",
            "--synthesize",
            "--api-key-env",
            "NONE",
        ]
    )

    manifest = json.loads((output_dir / "batch_manifest.json").read_text(encoding="utf-8"))
    rows = list(csv.DictReader((output_dir / "batch_summary.csv").read_text(encoding="utf-8-sig").splitlines()))
    assert exit_code == 0
    assert artifact_path.read_bytes() == artifact_before
    assert manifest["summary"]["skipped"] == 1
    assert rows[0]["status"] == "skipped"
    assert rows[0]["profile_validated"] == "true"


def test_resume_invalid_profile_reuses_raw_evidence_and_resynthesizes(tmp_path, monkeypatch):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    account = batch.load_accounts_from_csv(source)[0]
    raw = _raw_result(account)
    raw["synthesized_profile"] = {**_valid_hcp_profile(), "profile_completeness": 2.0}
    raw["synthesis_error"] = "stale failure"
    batch.write_batch_json(output_dir / "CUSTOM-001.json", raw)

    class UnexpectedProvider:
        @staticmethod
        def handle(_arguments):
            pytest.fail("invalid resume must reuse raw evidence")

    monkeypatch.setattr(batch, "_TOOL_MODULES", {name: UnexpectedProvider for name in batch.HCP_TOOLS})
    _install_provider_fakes(monkeypatch, synthesis_by_name={"Alex Tan": _valid_hcp_profile()})
    monkeypatch.setattr(batch, "_TOOL_MODULES", {name: UnexpectedProvider for name in batch.HCP_TOOLS})

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--resume",
            "--synthesize",
            "--api-key-env",
            "NONE",
            "--concurrency",
            "1",
        ]
    )

    artifact = json.loads((output_dir / "CUSTOM-001.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert artifact["search_results"] == raw["search_results"]
    assert artifact["synthesized_profile"] == _valid_hcp_profile()
    assert "synthesis_error" not in artifact


def test_provider_credentials_are_redacted_from_console_and_all_batch_artifacts(tmp_path, monkeypatch, capsys):
    source = _write_csv(
        tmp_path / "accounts.csv",
        "CUSTOM-001,Alex Tan,Oncology,Singapore,HCP,Example Hospital\n",
    )
    output_dir = tmp_path / "output"
    sentinels = {
        "CUSTOM_SYNTHESIS_API_KEY": "custom-synthesis-sentinel",
        "EXA_API_KEY": "exa-sentinel",
        "SERPER_API_KEY": "serper-sentinel",
        "TAVILY_API_KEY": "tavily-sentinel",
        "NCBI_API_KEY": "ncbi-sentinel",
    }
    for key, value in sentinels.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("shared.env._config_cache", {})
    leaked = " ".join(sentinels.values())

    class FailingProvider:
        @staticmethod
        def handle(_arguments):
            raise urllib.error.HTTPError("https://example.test", 400, leaked, {}, None)

    class FailingCompletions:
        @staticmethod
        def create(**_kwargs):
            raise RuntimeError(leaked)

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FailingCompletions())

    monkeypatch.setattr(batch, "_TOOL_MODULES", {name: FailingProvider for name in batch.HCP_TOOLS})
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(batch.time, "sleep", lambda _seconds: None)

    exit_code = batch_cli.main(
        [
            "--input-file",
            str(source),
            "--output-dir",
            str(output_dir),
            "--synthesize",
            "--api-key-env",
            "CUSTOM_SYNTHESIS_API_KEY",
            "--concurrency",
            "1",
        ]
    )

    captured = capsys.readouterr()
    persisted = b"\n".join(path.read_bytes() for path in sorted(output_dir.iterdir()) if path.is_file())
    assert exit_code == 1
    assert "[REDACTED]" in captured.err
    for sentinel in sentinels.values():
        assert sentinel not in captured.out
        assert sentinel not in captured.err
        assert sentinel.encode() not in persisted
