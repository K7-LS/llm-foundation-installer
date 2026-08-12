import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_officecli_golden_contract_binds_known_risk_routes():
    contract = json.loads((ROOT / "support" / "officecli-golden-suite.json").read_text(encoding="utf-8"))
    assert contract["officecli_version"] == "1.0.143"
    assert contract["schema_reference"]["minimum_elements"] == {"docx": 45, "xlsx": 41, "pptx": 34}
    assert contract["known_routes"] == {
        "csv_import": "k7-deterministic-csv-batch-adapter",
        "sparkline_edit": "excel-com-or-codex-fallback",
        "xlsx_range_screenshot": "not-acceptance-evidence",
        "html_render": "diagnostic-only",
        "pdf_export": "k7-officecli-pdf-exporter",
    }
    for format_name in ("docx", "xlsx", "pptx", "pdf"):
        assert "document-quality-gate" in contract["acceptance"][format_name]
