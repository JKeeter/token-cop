#!/usr/bin/env python3
"""Claude Code PreToolUse hook: block reads of heavyweight binary files.

Receives JSON on stdin with tool_input.file_path. If the file has a heavy
extension (.pdf, .docx, .pptx, .xlsx), prints a message and exits 2 to
block the read. Otherwise exits 0 silently.
"""
import json
import sys


HEAVY_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx"}


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return 0

    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return 0

    # Check extension
    ext = ""
    dot_idx = file_path.rfind(".")
    if dot_idx != -1:
        ext = file_path[dot_idx:].lower()

    if ext in HEAVY_EXTENSIONS:
        print(
            f"BLOCKED: '{file_path}' is a heavyweight file ({ext}). "
            f"Convert it first with:\n\n"
            f"  python scripts/convert_heavy_file.py \"{file_path}\"\n\n"
            f"Then read the converted artifacts from the .converted/ directory.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
