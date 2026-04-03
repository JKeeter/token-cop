#!/usr/bin/env python3
"""Convert heavyweight files (PDF, DOCX, PPTX, XLSX) into markdown/CSV artifacts.

Three-tier conversion strategy:
  Tier 1 (Native): openpyxl, python-pptx, python-docx for deterministic extraction
  Tier 2 (General): markitdown as fallback for PDF and anything Tier 1 misses
  Tier 3 (OCR fallback): pdfplumber for page-by-page text + density analysis

Usage:
    python scripts/convert_heavy_file.py <input_file> [--output-dir <dir>] [--prefer auto|native|markitdown]
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import mimetypes
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


PREVIEW_LINE_LIMIT = 12
PREVIEW_CHAR_LIMIT = 160

HEAVY_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx"}


@dataclass
class Artifact:
    path: str
    kind: str
    description: str


@dataclass
class ConversionResult:
    source: Path
    output_dir: Path
    converter: str = ""
    artifacts: list[Artifact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)
    stats: dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert heavyweight files into markdown, CSV, and an index."
    )
    parser.add_argument("source", type=Path, help="Path to the source file")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for converted outputs. Defaults to <source>.converted/",
    )
    parser.add_argument(
        "--prefer",
        choices=["auto", "native", "markitdown"],
        default="auto",
        help="Preferred converter strategy (default: auto)",
    )
    return parser.parse_args()


def slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-") or "sheet"


def require_module(module_name: str, package_name: str):
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise RuntimeError(
            f"Missing dependency '{package_name}'. "
            f"Install with: pip install {package_name}"
        ) from exc


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def relpath(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


def clean_preview_line(line: str) -> str:
    line = re.sub(r"\s+", " ", line).strip()
    if len(line) > PREVIEW_CHAR_LIMIT:
        return f"{line[:PREVIEW_CHAR_LIMIT - 1]}…"
    return line


def gather_preview_lines(path: Path) -> list[str]:
    if not path.exists() or path.suffix.lower() not in {".md", ".txt", ".csv"}:
        return []
    previews: list[str] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = clean_preview_line(raw_line)
        if not line:
            continue
        previews.append(line)
        if len(previews) >= PREVIEW_LINE_LIMIT:
            break
    return previews


def infer_next_step(result: ConversionResult) -> str:
    flags = set(result.quality_flags)
    if "dependency_missing" in flags:
        return "install_dependency_and_retry"
    if "conversion_failed" in flags:
        return "manual_review"
    if flags & {"scanned_pdf_suspected", "low_text_density"}:
        return "ocr_or_stronger_converter"
    if "low_text_output" in flags and result.source.suffix.lower() in HEAVY_EXTENSIONS:
        return "ocr_or_stronger_converter"
    return "read_extracted_artifact"


# ---------------------------------------------------------------------------
# Index generation
# ---------------------------------------------------------------------------

def build_index_markdown(result: ConversionResult) -> str:
    source_type = result.source.suffix.lower().lstrip(".") or "unknown"
    lines = [
        f"# Converted File Index: {result.source.name}",
        "",
        f"- Source path: `{result.source}`",
        f"- Source type: `{source_type}`",
        f"- Source size: `{human_size(result.source.stat().st_size)}`",
        f"- Converter: `{result.converter or 'none'}`",
        f"- Recommended next step: `{infer_next_step(result)}`",
    ]
    if result.quality_flags:
        lines.append(f"- Quality flags: `{', '.join(result.quality_flags)}`")
    if result.warnings:
        lines.append(f"- Warnings: `{'; '.join(result.warnings)}`")

    lines.extend(["", "## Artifacts", ""])
    for artifact in result.artifacts:
        lines.append(
            f"- `{artifact.kind}`: `{relpath(Path(artifact.path), result.output_dir)}` "
            f"-- {artifact.description}"
        )

    if result.stats:
        lines.extend(["", "## Stats", ""])
        for key, value in sorted(result.stats.items()):
            lines.append(f"- `{key}`: `{value}`")

    previews: list[str] = []
    for artifact in result.artifacts:
        previews.extend(gather_preview_lines(Path(artifact.path)))
        if len(previews) >= PREVIEW_LINE_LIMIT:
            break
    if previews:
        lines.extend(["", "## Preview", ""])
        for i, preview in enumerate(previews[:PREVIEW_LINE_LIMIT], 1):
            lines.append(f"{i}. {preview}")

    return "\n".join(lines) + "\n"


def write_index_files(result: ConversionResult) -> None:
    source_bytes = result.source.stat().st_size
    output_bytes = sum(
        Path(a.path).stat().st_size for a in result.artifacts if Path(a.path).exists()
    )
    result.stats["input_bytes"] = source_bytes
    result.stats["output_bytes"] = output_bytes
    result.stats["compression_ratio"] = (
        round(source_bytes / output_bytes, 1) if output_bytes else 0
    )

    index_payload = {
        "source": str(result.source),
        "source_name": result.source.name,
        "mime_type": mimetypes.guess_type(result.source.name)[0] or "application/octet-stream",
        "converter": result.converter,
        "artifacts": [a.__dict__ for a in result.artifacts],
        "warnings": result.warnings,
        "quality_flags": result.quality_flags,
        "recommended_next_step": infer_next_step(result),
        "statistics": {
            "input_bytes": source_bytes,
            "output_bytes": output_bytes,
            "compression_ratio": result.stats["compression_ratio"],
            "page_count": result.stats.get("page_count"),
            "sheet_count": result.stats.get("sheet_count"),
        },
    }

    write_text(result.output_dir / "index.json", json.dumps(index_payload, indent=2) + "\n")
    write_text(result.output_dir / "index.md", build_index_markdown(result))


# ---------------------------------------------------------------------------
# markitdown fallback (Tier 2)
# ---------------------------------------------------------------------------

def maybe_markitdown(source: Path, output_path: Path, prefer: str) -> str | None:
    if prefer == "native":
        return None

    commands: list[list[str]] = []
    if shutil.which("markitdown"):
        commands.append(["markitdown", str(source)])

    for cmd in commands:
        try:
            completed = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError):
            continue
        if completed.stdout.strip():
            write_text(output_path, completed.stdout)
            return "markitdown"

    # Try as Python library
    if prefer != "native":
        try:
            md = importlib.import_module("markitdown")
            converter = md.MarkItDown()
            result = converter.convert(str(source))
            if result.text_content and result.text_content.strip():
                write_text(output_path, result.text_content)
                return "markitdown"
        except (ImportError, Exception):
            pass

    return None


# ---------------------------------------------------------------------------
# Tier 1: Native converters
# ---------------------------------------------------------------------------

def convert_xlsx(source: Path, output_dir: Path) -> ConversionResult:
    openpyxl = require_module("openpyxl", "openpyxl")
    result = ConversionResult(source=source, output_dir=output_dir, converter="native-openpyxl")
    workbook = openpyxl.load_workbook(source, read_only=True, data_only=True)
    manifest_lines = [f"# Workbook: {source.name}", ""]
    sheet_count = 0

    for sheet in workbook.worksheets:
        sheet_count += 1
        slug = slugify(sheet.title)
        csv_path = output_dir / f"{sheet_count:02d}-{slug}.csv"

        row_count = 0
        max_cols = 0
        first_rows: list[list[str]] = []
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            for values in sheet.iter_rows(values_only=True):
                normalized = ["" if c is None else str(c) for c in values]
                writer.writerow(normalized)
                if any(c != "" for c in normalized):
                    row_count += 1
                    max_cols = max(max_cols, len(normalized))
                    if len(first_rows) < 5:
                        first_rows.append(normalized)

        header = first_rows[0] if first_rows else []
        manifest_lines.extend([
            f"## Sheet {sheet_count}: {sheet.title}",
            "",
            f"- Rows with content: `{row_count}`",
            f"- Columns: `{max_cols}`",
            f"- Header: `{header}`",
            f"- CSV artifact: `{csv_path.name}`",
            "",
        ])
        result.artifacts.append(
            Artifact(path=str(csv_path), kind="csv", description=f"Sheet {sheet_count}: {sheet.title}")
        )

    manifest_path = output_dir / "workbook.md"
    write_text(manifest_path, "\n".join(manifest_lines))
    result.artifacts.append(
        Artifact(path=str(manifest_path), kind="markdown", description="Workbook manifest")
    )
    result.stats["sheet_count"] = sheet_count
    if sheet_count == 0:
        result.quality_flags.append("low_text_output")
    return result


def extract_shape_text(shape, collector: list[str]) -> None:
    if hasattr(shape, "has_text_frame") and shape.has_text_frame:
        text = shape.text.strip()
        if text:
            collector.append(text)
    if hasattr(shape, "shapes"):
        for child in shape.shapes:
            extract_shape_text(child, collector)


def convert_pptx(source: Path, output_dir: Path) -> ConversionResult:
    pptx = require_module("pptx", "python-pptx")
    presentation = pptx.Presentation(str(source))
    result = ConversionResult(source=source, output_dir=output_dir, converter="native-python-pptx")
    md_path = output_dir / "presentation.md"

    lines = [f"# Presentation: {source.name}", ""]
    for slide_num, slide in enumerate(presentation.slides, 1):
        title_shape = slide.shapes.title
        title = title_shape.text.strip() if title_shape and title_shape.text else f"Slide {slide_num}"
        lines.append(f"## Slide {slide_num}: {title}")
        lines.append("")

        slide_text: list[str] = []
        for shape in slide.shapes:
            extract_shape_text(shape, slide_text)

        seen: set[str] = set()
        for block in slide_text:
            norm = block.strip()
            if not norm or norm in seen or norm == title:
                continue
            seen.add(norm)
            lines.append(f"- {norm}")

        if not seen:
            lines.append("- No extractable text on this slide.")

        notes_slide = getattr(slide, "notes_slide", None)
        notes_frame = getattr(notes_slide, "notes_text_frame", None) if notes_slide else None
        notes_text = notes_frame.text.strip() if notes_frame and notes_frame.text else ""
        if notes_text:
            lines.extend(["", "### Speaker Notes", "", notes_text])
        lines.append("")

    write_text(md_path, "\n".join(lines))
    result.artifacts.append(
        Artifact(path=str(md_path), kind="markdown", description="Slide outline and notes")
    )
    result.stats["slide_count"] = len(presentation.slides)
    if len(presentation.slides) == 0:
        result.quality_flags.append("low_text_output")
    return result


def convert_docx(source: Path, output_dir: Path, prefer: str) -> ConversionResult:
    md_path = output_dir / "document.md"

    # Tier 2: try markitdown first if preferred
    if prefer != "native":
        used = maybe_markitdown(source, md_path, prefer)
        if used:
            result = ConversionResult(source=source, output_dir=output_dir, converter=used)
            result.artifacts.append(
                Artifact(path=str(md_path), kind="markdown", description="Document converted to markdown")
            )
            text_chars = len(md_path.read_text(encoding="utf-8", errors="replace"))
            result.stats["text_chars"] = text_chars
            if text_chars < 200:
                result.quality_flags.append("low_text_output")
            return result

    # Tier 1: native python-docx
    docx = require_module("docx", "python-docx")
    document = docx.Document(str(source))
    result = ConversionResult(source=source, output_dir=output_dir, converter="native-python-docx")

    lines = [f"# Document: {source.name}", ""]
    heading_count = 0
    paragraph_count = 0
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style_name = para.style.name if para.style else ""
        match = re.match(r"Heading (\d+)", style_name)
        if match:
            level = min(int(match.group(1)), 6)
            lines.append(f"{'#' * level} {text}")
            lines.append("")
            heading_count += 1
        else:
            lines.append(text)
            lines.append("")
            paragraph_count += 1

    for table_idx, table in enumerate(document.tables, 1):
        rows = []
        for row in table.rows[:11]:
            rows.append([cell.text.strip() for cell in row.cells])
        if not rows:
            continue
        lines.append(f"## Table {table_idx}")
        lines.append("")
        header = rows[0]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join(["---"] * len(header)) + " |")
        for row in rows[1:]:
            lines.append("| " + " | ".join(row) + " |")
        if len(table.rows) > 11:
            lines.extend(["", f"_Table truncated after 11 rows for token efficiency._"])
        lines.append("")

    write_text(md_path, "\n".join(lines))
    result.artifacts.append(
        Artifact(path=str(md_path), kind="markdown", description="Document converted to markdown")
    )
    result.stats.update({
        "heading_count": heading_count,
        "paragraph_count": paragraph_count,
        "table_count": len(document.tables),
    })
    if heading_count == 0 and paragraph_count < 5:
        result.quality_flags.append("low_text_output")
    return result


# ---------------------------------------------------------------------------
# Tier 2/3: PDF conversion
# ---------------------------------------------------------------------------

def convert_pdf(source: Path, output_dir: Path, prefer: str) -> ConversionResult:
    md_path = output_dir / "document.md"

    # Tier 2: markitdown
    if prefer != "native":
        used = maybe_markitdown(source, md_path, prefer)
        if used:
            result = ConversionResult(source=source, output_dir=output_dir, converter=used)
            result.artifacts.append(
                Artifact(path=str(md_path), kind="markdown", description="PDF converted to markdown")
            )
            text_chars = len(md_path.read_text(encoding="utf-8", errors="replace"))
            result.stats["text_chars"] = text_chars
            if text_chars < 400:
                result.quality_flags.append("low_text_output")
            return result

    # Tier 3: pdfplumber page-by-page with density analysis
    pdfplumber = require_module("pdfplumber", "pdfplumber")
    result = ConversionResult(source=source, output_dir=output_dir, converter="native-pdfplumber")

    lines = [f"# PDF: {source.name}", ""]
    non_empty_pages = 0
    char_count = 0
    with pdfplumber.open(str(source)) as pdf:
        page_count = len(pdf.pages)
        for page_num, page in enumerate(pdf.pages, 1):
            text = (page.extract_text() or "").strip()
            lines.append(f"## Page {page_num}")
            lines.append("")
            if text:
                lines.append(text)
                non_empty_pages += 1
                char_count += len(text)
            else:
                lines.append("_No extractable text on this page._")
            lines.append("")

    write_text(md_path, "\n".join(lines))
    result.artifacts.append(
        Artifact(path=str(md_path), kind="markdown", description="PDF text extracted page by page")
    )
    result.stats.update({
        "page_count": page_count,
        "non_empty_pages": non_empty_pages,
        "text_chars": char_count,
        "avg_chars_per_page": round(char_count / page_count, 1) if page_count else 0,
    })
    if page_count and non_empty_pages / page_count < 0.7:
        result.quality_flags.append("scanned_pdf_suspected")
    if page_count >= 3 and char_count / page_count < 120:
        result.quality_flags.append("low_text_density")
    return result


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def handle_conversion(source: Path, output_dir: Path, prefer: str) -> ConversionResult:
    suffix = source.suffix.lower()
    if suffix == ".xlsx":
        return convert_xlsx(source, output_dir)
    if suffix == ".pptx":
        return convert_pptx(source, output_dir)
    if suffix == ".docx":
        return convert_docx(source, output_dir, prefer)
    if suffix == ".pdf":
        return convert_pdf(source, output_dir, prefer)

    result = ConversionResult(source=source, output_dir=output_dir, converter="unsupported")
    result.warnings.append(f"Unsupported file type: {suffix or 'unknown'}")
    result.quality_flags.extend(["conversion_failed", "unsupported_type"])
    return result


def main() -> int:
    args = parse_args()
    source = args.source.expanduser().resolve()
    if not source.exists() or not source.is_file():
        print(f"Source file not found: {source}", file=sys.stderr)
        return 1

    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else source.parent / f"{source.name}.converted"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = handle_conversion(source, output_dir, args.prefer)
    except RuntimeError as exc:
        result = ConversionResult(source=source, output_dir=output_dir, converter="failed")
        result.warnings.append(str(exc))
        result.quality_flags.extend(["conversion_failed", "dependency_missing"])
    except Exception as exc:
        result = ConversionResult(source=source, output_dir=output_dir, converter="failed")
        result.warnings.append(f"{exc.__class__.__name__}: {exc}")
        result.quality_flags.extend(["conversion_failed", "unexpected_error"])

    result.stats.setdefault("source_extension", source.suffix.lower() or "none")
    result.stats.setdefault("source_size_bytes", source.stat().st_size)
    write_index_files(result)

    print(json.dumps({
        "output_dir": str(output_dir),
        "recommended_next_step": infer_next_step(result),
        "quality_flags": result.quality_flags,
        "artifacts": [a.__dict__ for a in result.artifacts],
    }, indent=2))
    return 0 if "conversion_failed" not in result.quality_flags else 2


if __name__ == "__main__":
    sys.exit(main())
