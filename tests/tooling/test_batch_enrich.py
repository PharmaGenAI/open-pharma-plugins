import importlib.util
from pathlib import Path

from open_pharma_plugins_hcp_intelligence import batch_cli


def test_repository_wrapper_exports_packaged_main():
    path = Path(__file__).resolve().parents[2] / "scripts" / "batch_enrich.py"
    spec = importlib.util.spec_from_file_location("batch_enrich_wrapper", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main is batch_cli.main
