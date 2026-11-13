#!/usr/bin/env python3
"""
Combine the text of every file in a folder tree into one .txt file,
grouped by subfolder.

.docx files are read with python-docx (paragraphs and tables, in order).
Files with a known text/code extension are read directly.
Any other file is probed for text; if it looks binary, it is reported
but its contents are not included.

The script skips itself and its own output file so that re-runs do not
grow the report by embedding previous runs.

Usage:
    python combine_files.py
    python combine_files.py --root "C:\\path\\to\\folder"
    python combine_files.py --root "C:\\path\\to\\folder" --output combined.txt

With no arguments, it scans the current folder and writes a .txt file
named after that folder (for example, scanning a folder called "notes"
writes "notes.txt" inside it).
"""

# MODULE:    combine_files.py
# PURPOSE:   Combine the text of every file under a folder tree into one .txt file, grouped by subfolder.
# READS:     <root folder>  (any regular files found there and in its subfolders)
# WRITES:    <root>/<output>.txt  (the combined report; default name is the root folder's own name; overwritten on each run)
# CALLED BY: User (command line)
# CALLS:     None  (imports python-docx lazily, only when a .docx is encountered)
# ERRORS:    Fatal      -- root is not a folder, or the output file cannot be written. Script exits non-zero.
#            Skippable  -- a single file cannot be read, or looks binary. Reason is written into the report; scan continues.

import argparse
import os
import sys

SEPARATOR = "-" * 33

# Used only when the root folder has no usable name (e.g. "/" or "C:\\").
FALLBACK_OUTPUT_STEM = "all_contents"

TEXT_EXTENSIONS = {
    # Documents
    ".txt", ".md", ".markdown", ".rst", ".log", ".tex", ".bib",
    # Code
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".java",
    ".go", ".rs", ".rb", ".php", ".pl", ".lua", ".r",
    ".sh", ".bash", ".zsh", ".fish", ".bat", ".cmd", ".ps1",
    # Web
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".svg",
    # Data / config
    ".xml", ".json", ".jsonl", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf",
    ".csv", ".tsv", ".sql",
}

TEXT_FILENAMES = {
    "makefile", "dockerfile", "readme", "license", "authors",
    ".gitignore", ".gitattributes", ".editorconfig",
    ".env", ".flake8", ".pylintrc",
}

DOCX_EXTENSIONS = {".docx"}


# ---- docx handling ---------------------------------------------------------

def iter_docx_block_items(document):
    """Yield each paragraph and table in the order they appear in a docx."""
    from docx.oxml.ns import qn
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph as DocxParagraph

    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield DocxParagraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield DocxTable(child, document)


def extract_docx_text(path):
    """Return the readable text of a .docx file: paragraphs and tables, in order."""
    from docx import Document
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph as DocxParagraph

    document = Document(path)
    parts = []
    for block in iter_docx_block_items(document):
        if isinstance(block, DocxParagraph):
            parts.append(block.text)
        else:
            for row in block.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)


# ---- generic text handling -------------------------------------------------

def read_text(path):
    """Read a text file, trying a few encodings before falling back to replace."""
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def looks_binary(path, sample_size=4096):
    """Heuristic: NUL bytes or a high ratio of non-printable bytes -> binary."""
    try:
        with open(path, "rb") as f:
            sample = f.read(sample_size)
    except OSError:
        return True
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    nontext = sum(1 for b in sample if b < 9 or (13 < b < 32) or b == 127)
    return nontext / len(sample) > 0.30


def classify(path):
    """Return 'docx', 'text', or 'unknown' for a given file."""
    ext = os.path.splitext(path)[1].lower()
    if ext in DOCX_EXTENSIONS:
        return "docx"
    if ext in TEXT_EXTENSIONS:
        return "text"
    name = os.path.basename(path).lower()
    if name in TEXT_FILENAMES:
        return "text"
    return "unknown"


def extract_any(path):
    """Return (content, note). Exactly one of the two is non-None."""
    kind = classify(path)

    if kind == "docx":
        try:
            return extract_docx_text(path), None
        except ImportError:
            return None, "python-docx is not installed; .docx file skipped."
        except Exception as exc:
            return None, f"Could not read this .docx file: {exc}"

    if kind == "text":
        try:
            return read_text(path), None
        except Exception as exc:
            return None, f"Could not read this file: {exc}"

    # Unknown extension: try as text only if it does not look binary.
    if looks_binary(path):
        return None, "Binary file; contents not included."
    try:
        return read_text(path), None
    except Exception as exc:
        return None, f"Could not read this file: {exc}"


# ---- report assembly -------------------------------------------------------

def display_path(relative_path):
    """Turn an os.path.relpath result into the '\\folder\\file' style used in the report."""
    return "\\" + relative_path.replace(os.sep, "\\")


def collect_skip_paths(output_path):
    """Absolute paths to exclude: this script and the output file."""
    skip_paths = {os.path.abspath(output_path)}

    if "__file__" in globals():
        this_script = os.path.abspath(__file__)
        skip_paths.add(this_script)
        if this_script.endswith(".pyc"):
            skip_paths.add(this_script[:-1])
    return skip_paths


def find_files(dirpath, filenames, skip_paths):
    """Return real files, sorted, skipping the output, this script, and Word lock files."""
    return sorted(
        name for name in filenames
        if os.path.join(dirpath, name) not in skip_paths
        and not name.startswith("~$")
    )


def build_report(root, output_path):
    skip_paths = collect_skip_paths(output_path)
    lines = [SEPARATOR, "root folder -", SEPARATOR, ""]

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()  # visit subfolders in alphabetical order
        files = find_files(dirpath, filenames, skip_paths)
        if not files:
            continue

        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir != ".":
            lines.append(display_path(rel_dir) + "\\")
            lines.append("")

        for filename in files:
            full_path = os.path.join(dirpath, filename)
            rel_file = filename if rel_dir == "." else os.path.join(rel_dir, filename)

            content, note = extract_any(full_path)

            lines.append(display_path(rel_file) + " -")
            lines.append("")
            if content is not None:
                lines.append('"""' + content + '"""')
            else:
                lines.append(f'"""[{note}]"""')
            lines.append("")

    lines.append(SEPARATOR)
    lines.append("END OF FILE")
    lines.append(SEPARATOR)
    return "\n".join(lines)


# ---- entry point -----------------------------------------------------------

def default_output_name(root_abs):
    """Return '<root folder name>.txt', or the fallback when root has no name."""
    stem = os.path.basename(root_abs)
    if not stem:
        stem = FALLBACK_OUTPUT_STEM
    return stem + ".txt"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Combine every file under a folder into one .txt file."
    )
    parser.add_argument(
        "--root",
        default=os.getcwd(),
        help="Folder to scan (default: the current folder).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Name of the .txt file to create "
             "(default: the root folder's own name plus .txt).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    root = os.path.abspath(args.root)

    if not os.path.isdir(root):
        sys.exit(f"'{root}' is not a folder.")

    output_name = args.output if args.output else default_output_name(root)
    output_path = os.path.abspath(os.path.join(root, output_name))
    report = build_report(root, output_path)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)
    except OSError as exc:
        sys.exit(f"Could not write '{output_path}': {exc}")

    print(f"Done. Combined text written to: {output_path}")


if __name__ == "__main__":
    main()