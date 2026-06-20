"""Generate ordinances_index.json from the ordinances/ markdown files."""

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
ORDINANCES_DIR = PROJECT_ROOT / "ordinances"
OUTPUT_FILE = PROJECT_ROOT / "data" / "ordinances_index.json"

# Matches: ### Sec. 38-8. Security alarm systems.
# Also:   ## Sec. 1-1. Title. (H2 level used in some chapters)
SECTION_RE = re.compile(r"^#{2,4}\s+Sec\.\s+([\w-]+)\.\s+(.+?)\.?\s*$")

# Chapter number from filename: Chapter-38-Offenses.md -> ("38", "Offenses")
CHAPTER_RE = re.compile(r"^Chapter-(\d+)-(.+)\.md$")


def title_from_filename(filename: str) -> str:
    m = CHAPTER_RE.match(filename)
    if not m:
        return filename
    return m.group(2).replace("-", " ")


def parse_file(path: Path) -> list[dict]:
    """Extract sections from a single ordinance markdown file."""
    filename = path.name
    m = CHAPTER_RE.match(filename)
    if not m:
        return []

    chapter_num = m.group(1)
    chapter_name = m.group(2).replace("-", " ")

    entries = []
    with open(path) as f:
        lines = f.readlines()

    for i, line in enumerate(lines, 1):
        sec_match = SECTION_RE.match(line.strip())
        if sec_match:
            section = sec_match.group(1)
            title = sec_match.group(2).strip().rstrip(".")
            entries.append({
                "chapter": chapter_num,
                "chapter_name": chapter_name,
                "filename": filename,
                "section": section,
                "title": title,
                "line_start": i,
            })

    return entries


def main():
    all_entries = []

    # Sort files by chapter number
    files = sorted(
        ORDINANCES_DIR.glob("Chapter-*.md"),
        key=lambda p: int(CHAPTER_RE.match(p.name).group(1)) if CHAPTER_RE.match(p.name) else 999
    )

    for path in files:
        entries = parse_file(path)
        all_entries.extend(entries)
        print(f"  {path.name}: {len(entries)} sections")

    with open(OUTPUT_FILE, "w") as f:
        json.dump(all_entries, f, indent=2)

    print(f"\nWrote {len(all_entries)} entries to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
