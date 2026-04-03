---
name: heavy-file-ingestion
description: Convert heavyweight files (PDF, DOCX, PPTX, XLSX) into markdown/CSV before reading. Detects when a user references a heavy file and converts it first to save 10-20x on tokens.
---

# Heavy File Ingestion

## Problem

Reading binary files (PDF, DOCX, PPTX, XLSX) raw wastes massive amounts of tokens. A 4,500-word PDF can consume 100K+ tokens when read as binary. Converting to markdown first saves 10-20x.

## Trigger Conditions

- User asks to read, analyze, summarize, or extract from a PDF, DOCX, PPTX, or XLSX file
- User references a heavyweight file by path or name
- A PreToolUse hook blocks a Read on a heavy file extension

## Process

1. **Detect** the heavyweight file path and extension.

2. **Convert** using the bundled converter script:

```bash
python scripts/convert_heavy_file.py /absolute/path/to/file.ext
```

This creates a `<filename>.converted/` directory with extracted artifacts.

3. **Check quality** by reading `index.json` from the converted directory:

```bash
cat /path/to/file.ext.converted/index.json
```

Look at:
- `quality_flags`: if empty, extraction was clean
- `recommended_next_step`: tells you what to do next
- `statistics.compression_ratio`: how much smaller the output is

4. **Read the artifacts** instead of the original file:
   - For documents/PDFs: read `document.md`
   - For presentations: read `presentation.md`
   - For spreadsheets: read the per-sheet CSV files listed in `workbook.md`

5. **Handle quality flags**:
   - `low_text_output`: extraction got very little text; try `--prefer markitdown`
   - `scanned_pdf_suspected`: PDF is likely image-based; warn the user OCR may be needed
   - `low_text_density`: pages have very little text; may be charts/images
   - `dependency_missing`: install the missing package and retry

6. **Report token savings** to the user:
   - Estimate raw tokens: `file_size_bytes / 4` (rough binary token estimate)
   - Actual tokens: `output_bytes / 4` (text is ~4 bytes per token)
   - Savings: `1 - (output_bytes / input_bytes)` as a percentage

## Converter Options

```bash
# Auto mode (default): tries native first, falls back to markitdown
python scripts/convert_heavy_file.py file.pdf

# Force native converters only (no markitdown)
python scripts/convert_heavy_file.py file.pdf --prefer native

# Prefer markitdown for PDF/DOCX
python scripts/convert_heavy_file.py file.pdf --prefer markitdown

# Custom output directory
python scripts/convert_heavy_file.py file.pdf --output-dir /tmp/converted
```

## Policy

- **Convert before reading.** Never dump raw binary files into context.
- **Index before reasoning.** Read `index.json` first to assess quality.
- **Escalate by tier.** Tier 1 (native) is deterministic and free. Only escalate to markitdown if native extraction flags quality issues.
