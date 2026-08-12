"""Convert CSV to OfficeCLI set-cell batch operations without using broken import."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def column_name(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def convert(path: Path, sheet: str, delimiter: str) -> list[dict[str, object]]:
    if not sheet or "/" in sheet or sheet in (".", ".."):
        raise ValueError("invalid sheet name")
    if len(delimiter) != 1:
        raise ValueError("delimiter must be one character")
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=delimiter))
    width = len(rows[0]) if rows else 0
    if any(len(row) != width for row in rows):
        raise ValueError("ragged CSV rows are not allowed")
    operations = []
    for row_index, row in enumerate(rows, start=1):
        for column_index, value in enumerate(row):
            operations.append(
                {
                    "command": "set",
                    "path": f"/{sheet}/{column_name(column_index)}{row_index}",
                    "props": {"value": value},
                }
            )
    return operations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--delimiter", default=",")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = json.dumps(convert(args.csv, args.sheet, args.delimiter), ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
