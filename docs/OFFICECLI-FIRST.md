# OfficeCLI-first contract

Foundation pins OfficeCLI 1.0.143 and installs it with a lossless UTF-8 shim,
deterministic CSV-to-batch adapter and the signed-build K7 PDF exporter.
OfficeCLI is the primary structural reader/editor for DOCX, XLSX and PPTX.
Codex native document tools remain an explicit fallback and independent
acceptance path.

The golden-suite contract is
`support/officecli-golden-suite.json`. Every version change requires the
entire suite to be rerun; passing only smoke tests is insufficient.

## Fail-closed routes

- OfficeCLI plugin, MCP, skill installation and self-update stay disabled.
- CSV import is replaced by `officecli_csv_batch.py`.
- Workbooks containing sparklines use Excel COM or the Codex spreadsheet
  fallback.
- XLSX range screenshots and HTML render are diagnostic only.
- Final DOCX/XLSX/PPTX PDF output uses the K7 Office COM exporter when a
  compatible Office installation is available.
- Arbitrary editing of an existing PDF stays with the Codex PDF tool.

Final delivery additionally requires `document-quality-gate`, a native
render/read-back, the relevant file reviewer and source audit.
