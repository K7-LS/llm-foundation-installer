import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_csv_is_converted_to_deterministic_per_cell_batch(tmp_path: Path):
    source = tmp_path / "source.csv"
    source.write_text('Name;Count;Note\n"Duct, A";2;Тест\n', encoding="utf-8-sig")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "officecli_csv_batch.py"),
            str(source),
            "--sheet",
            "Импорт",
            "--delimiter",
            ";",
        ],
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value == [
        {"command": "set", "path": "/Импорт/A1", "props": {"value": "Name"}},
        {"command": "set", "path": "/Импорт/B1", "props": {"value": "Count"}},
        {"command": "set", "path": "/Импорт/C1", "props": {"value": "Note"}},
        {"command": "set", "path": "/Импорт/A2", "props": {"value": "Duct, A"}},
        {"command": "set", "path": "/Импорт/B2", "props": {"value": "2"}},
        {"command": "set", "path": "/Импорт/C2", "props": {"value": "Тест"}},
    ]


def test_csv_adapter_rejects_ragged_rows(tmp_path: Path):
    source = tmp_path / "bad.csv"
    source.write_text("a,b\n1\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "officecli_csv_batch.py"), str(source), "--sheet", "S"],
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "ragged" in result.stderr.lower()
