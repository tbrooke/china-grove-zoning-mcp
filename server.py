"""China Grove Zoning MCP Server.

Provides tools for looking up zoning ordinance information including
permitted uses, dimensional standards, district info, and special requirements.
"""

import json
import os
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import re

# Resolve data paths relative to this file's directory
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
MARKDOWN_DIR = PROJECT_ROOT / "markdown"
STATUTES_DIR = PROJECT_ROOT / "statutes"

DISTRICT_ORDER = [
    "R-P", "R-S", "R-T", "R-M", "R-MH",
    "N-C", "O-I", "C-B", "H-B",
    "C-P", "L-I", "H-I", "PUD",
]

PERMISSION_LABELS = {
    "X": "Permitted by Right",
    "C": "Conditional Zoning",
    "S": "Special Use",
}

# --- Data Loading ---

def load_json(filename: str):
    with open(DATA_DIR / filename) as f:
        return json.load(f)


def _load_permitted_uses():
    return load_json("permitted_uses.json")


def _load_dimensional_standards():
    return load_json("dimensional_standards.json")


def _load_districts():
    return load_json("districts.json")


def _load_special_requirements_index():
    return load_json("special_requirements_index.json")


def _load_subdivision_index():
    return load_json("subdivision_index.json")


def _load_general_provisions_index():
    return load_json("general_provisions_index.json")


# --- MCP Server ---

mcp = FastMCP(
    "China Grove Zoning",
    instructions=(
        "This server provides tools for researching zoning and land use questions "
        "for the Town of China Grove, NC. Use these tools to look up permitted uses, "
        "dimensional standards (setbacks, density, height), district information, "
        "special requirements, general provisions, and subdivision procedures from the "
        "Unified Development Ordinance (UDO). For subdivision questions (lot splits, plat "
        "approval, minor vs major thresholds, improvement requirements), use "
        "get_subdivision_requirements(). For Chapter 2 general provisions (lot standards, "
        "infill setback rules, corner lots, ROW observation), use get_general_provisions(). "
        "For parcel lookups by PIN, address, or owner name, use get_parcel_info() — it "
        "returns the parcel's zoning district, jurisdiction status, and property details. "
        "For infill development, use get_infill_context(pin) to find neighboring parcels "
        "within 300 ft for setback averaging under Section 2.2D. "
        "For NC state zoning law, use get_160d_section() or search_160d() to look up "
        "NCGS Chapter 160D. When the local UDO may conflict with state law, 160D controls. "
        "Always cite specific ordinance sections when answering."
    ),
)


@mcp.tool()
def lookup_permitted_use(use: str, district: str | None = None) -> str:
    """Look up whether a land use is permitted in a zoning district.

    Args:
        use: The land use to search for (e.g., "restaurant", "duplex", "single family").
             Partial matches are supported.
        district: Optional zoning district code to filter by (e.g., "C-B", "R-S", "H-B").
                  If omitted, returns permissions for all 13 districts.
    """
    uses = _load_permitted_uses()
    query = use.lower()
    district_upper = district.upper() if district else None

    if district_upper and district_upper not in DISTRICT_ORDER:
        return f"Unknown district '{district}'. Valid districts: {', '.join(DISTRICT_ORDER)}"

    matches = [u for u in uses if query in u["use"].lower()]

    if not matches:
        # Try category search
        matches = [u for u in uses if query in u.get("category", "").lower()]

    if not matches:
        return f"No uses found matching '{use}'. Try a broader search term."

    results = []
    for u in matches:
        lines = [f"## {u['use']}"]
        lines.append(f"**Category:** {u['category']}")
        if u.get("naics"):
            lines.append(f"**NAICS:** {u['naics']}")
        if u.get("special_requirements"):
            lines.append(f"**Special Requirements:** Section {u['special_requirements']}")
        if u.get("discrepancy_note"):
            lines.append(f"\n⚠ **Ordinance Discrepancy:** {u['discrepancy_note']}")

        if district_upper:
            perm = u["districts"].get(district_upper)
            if perm:
                lines.append(f"\n**{district_upper}:** {perm} — {PERMISSION_LABELS.get(perm, perm)}")
            else:
                lines.append(f"\n**{district_upper}:** NOT PERMITTED")
        else:
            lines.append("\n**District Permissions:**")
            for d in DISTRICT_ORDER:
                perm = u["districts"].get(d)
                if perm:
                    lines.append(f"- {d}: **{perm}** — {PERMISSION_LABELS.get(perm, perm)}")
                else:
                    lines.append(f"- {d}: not permitted")

        results.append("\n".join(lines))

    return "\n\n---\n\n".join(results)


@mcp.tool()
def get_dimensional_standards(district: str) -> str:
    """Get dimensional standards (setbacks, density, height, lot requirements) for a zoning district.

    Args:
        district: Zoning district code (e.g., "R-S", "C-B", "H-B").
    """
    standards = _load_dimensional_standards()
    d = district.upper()

    if d not in DISTRICT_ORDER:
        return f"Unknown district '{district}'. Valid districts: {', '.join(DISTRICT_ORDER)}"

    principal = [s for s in standards["principal_structures"] if s["district"] == d]
    accessory = [s for s in standards["accessory_structures"] if s["district"] == d]

    if not principal and not accessory:
        return f"No dimensional standards found for district {d}."

    lines = [f"# Dimensional Standards: {d}\n"]

    if principal:
        lines.append("## Principal Structures\n")
        for entry in principal:
            lines.append(f"### {entry['use_type']}")
            if entry.get("density"):
                lines.append(f"- **Density:** {entry['density']}")
            if entry.get("min_lot_size"):
                lines.append(f"- **Min Lot Size:** {entry['min_lot_size']}")
            if entry.get("min_width_ft") is not None:
                width = f"{entry['min_width_ft']}ft"
                if entry.get("min_width_alley_ft"):
                    width += f" ({entry['min_width_alley_ft']}ft with alley)"
                lines.append(f"- **Min Width:** {width}")
            if entry.get("min_street_frontage_ft") is not None:
                lines.append(f"- **Min Street Frontage:** {entry['min_street_frontage_ft']}ft")

            front = entry.get("setback_front_ft")
            front_max = entry.get("setback_front_max_ft")
            if front is not None:
                front_str = f"{front}ft"
                if front_max:
                    front_str += f" (max {front_max}ft)"
                lines.append(f"- **Setback Front:** {front_str}")

            side_min = entry.get("setback_side_min_ft")
            side_note = entry.get("setback_side_interior_note")
            if side_min is not None:
                lines.append(f"- **Setback Side:** {side_note if side_note else f'{side_min}ft'}")

            if entry.get("setback_rear_ft") is not None:
                lines.append(f"- **Setback Rear:** {entry['setback_rear_ft']}ft")
            if entry.get("max_height_ft") is not None:
                lines.append(f"- **Max Height:** {entry['max_height_ft']}ft")
            lines.append("")

    if accessory:
        lines.append("## Accessory Structures\n")
        for entry in accessory:
            side_note = entry.get("setback_side_interior_note")
            side_int = entry.get("setback_side_interior_ft")
            lines.append(f"- **Side Interior:** {side_note if side_note else f'{side_int}ft'}")
            if entry.get("setback_side_corner"):
                lines.append(f"- **Side Corner:** {entry['setback_side_corner']}")
            rear_note = entry.get("setback_rear_note")
            rear = entry.get("setback_rear_ft")
            lines.append(f"- **Rear:** {rear_note if rear_note else f'{rear}ft'}")

    # Infill lot rule — applies to ALL districts
    infill = standards.get("infill_lot_rule")
    if infill:
        lines.append("\n## Infill Lot Rule (Section 2.2D)")
        lines.append(f"**Applies to:** {infill['applies_to']}")
        lines.append(f"\n{infill['rule']}")
        lines.append(f"\n*{infill['effect']}*")
        lines.append(f"\n*{infill['calculation_note']}*")

    notes = standards.get("notes", {})
    if notes:
        lines.append("\n## Notes")
        for key, val in notes.items():
            lines.append(f"- **{key}:** {val}")

    return "\n".join(lines)


@mcp.tool()
def get_district_info(district_code: str) -> str:
    """Get information about a zoning district including its intent, character, and key rules.

    Args:
        district_code: Zoning district code (e.g., "R-P", "C-B", "H-B", "PUD").
                       Use "all" to list all districts.
    """
    districts = _load_districts()
    code = district_code.upper()

    if code == "ALL":
        lines = ["# China Grove Zoning Districts\n"]
        for c, info in districts.items():
            lines.append(f"## {c} — {info['name']}")
            lines.append(f"Section {info['section']} | Character: {info['character']}")
            lines.append(f"{info['intent']}\n")
        return "\n".join(lines)

    if code not in districts:
        return f"Unknown district '{district_code}'. Valid: {', '.join(districts.keys())}"

    info = districts[code]
    lines = [
        f"# {code} — {info['name']}",
        f"**Section:** {info['section']}",
        f"**Character:** {info['character']}",
        f"\n**Intent:** {info['intent']}",
    ]
    if info.get("key_rules"):
        lines.append("\n**Key Rules:**")
        for rule in info["key_rules"]:
            lines.append(f"- {rule}")

    return "\n".join(lines)


@mcp.tool()
def get_special_requirements(section: str) -> str:
    """Get special requirements for a specific use from Chapter 8 of the UDO.

    Args:
        section: Section number (e.g., "8.22") or keyword to search titles
                 (e.g., "home occupation", "microbrewery").
    """
    index = _load_special_requirements_index()

    # Try exact section match
    matches = [s for s in index if s["section"] == section]

    if not matches:
        # Try partial section match
        matches = [s for s in index if s["section"].startswith(section)]

    if not matches:
        # Try title keyword search
        matches = [s for s in index if section.lower() in s["title"].lower()]

    if not matches:
        # Try summary keyword search
        matches = [s for s in index if section.lower() in s["summary"].lower()]

    if not matches:
        lines = [f"No special requirement found for '{section}'.\n", "**Available sections:**"]
        for s in index:
            lines.append(f"- **{s['section']}:** {s['title']}")
        return "\n".join(lines)

    results = []
    for match in matches:
        lines = [f"## Section {match['section']}: {match['title']}"]
        lines.append(f"\n**Summary:** {match['summary']}")

        # Try to load full text from markdown
        md_path = MARKDOWN_DIR / "Chapter-08-Special-Requirements.md"
        if md_path.exists():
            with open(md_path) as f:
                all_lines = f.readlines()
            start = match["line_start"] - 1
            # Find the next section header to determine end
            end = len(all_lines)
            for i in range(start + 1, len(all_lines)):
                if all_lines[i].startswith("## Section 8."):
                    end = i
                    break
            full_text = "".join(all_lines[start:end]).strip()
            lines.append(f"\n**Full Text:**\n\n{full_text}")

        results.append("\n".join(lines))

    return "\n\n---\n\n".join(results)


@mcp.tool()
def get_general_provisions(section: str) -> str:
    """Get general provisions from Chapter 2 of the UDO.

    Covers: lot standards, infill setback rules (300-ft average), corner lots,
    lot line orientation, right-of-way observation, street frontage requirements,
    essential services exemptions, and front setback encroachments.

    Args:
        section: Section number (e.g., "2.2D", "2.2", "2.1") or keyword to search
                 (e.g., "infill", "corner lot", "right-of-way", "encroachment").
                 Use "all" for the complete Chapter 2 text.
    """
    index = _load_general_provisions_index()

    if section.lower().strip() == "all":
        # Return full Chapter 2 text
        md_path = MARKDOWN_DIR / "Chapter-02-General-Provisions.md"
        if md_path.exists():
            with open(md_path) as f:
                return f.read()
        return "Chapter 2 markdown file not found."

    # Try exact section match
    matches = [s for s in index if s["section"] == section]

    if not matches:
        # Try partial section match (e.g., "2.2" matches 2.2, 2.2A, 2.2B, etc.)
        matches = [s for s in index if s["section"].startswith(section)]

    if not matches:
        # Try keyword search in title
        q = section.lower()
        matches = [s for s in index if q in s["title"].lower()]

    if not matches:
        # Try keyword search in summary
        q = section.lower()
        matches = [s for s in index if q in s["summary"].lower()]

    if not matches:
        lines = [f"No general provision found for '{section}'.\n", "**Available sections:**"]
        for s in index:
            lines.append(f"- **{s['section']}:** {s['title']}")
        return "\n".join(lines)

    results = []
    for match in matches:
        lines = [f"## Section {match['section']}: {match['title']}"]
        lines.append(f"\n**Summary:** {match['summary']}")

        # Load full text from markdown
        md_path = MARKDOWN_DIR / "Chapter-02-General-Provisions.md"
        if md_path.exists():
            with open(md_path) as f:
                all_lines = f.readlines()
            start = match["line_start"] - 1
            # Find next section header or subsection to determine end
            end = len(all_lines)
            for i in range(start + 1, len(all_lines)):
                line = all_lines[i]
                # Stop at next ## section header
                if line.startswith("## Section"):
                    end = i
                    break
                # For subsections (2.2A, 2.2B, etc.), stop at the next lettered subsection
                if match["section"].startswith("2.2") and len(match["section"]) > 3:
                    # This is a sub-provision like 2.2D — stop at next lettered line
                    if line.strip() and line[0].isupper() and len(line) > 2 and line[1] == ".":
                        end = i
                        break
            full_text = "".join(all_lines[start:end]).strip()
            lines.append(f"\n**Full Text:**\n\n{full_text}")

        results.append("\n".join(lines))

    return "\n\n---\n\n".join(results)


@mcp.tool()
def get_subdivision_requirements(query: str) -> str:
    """Get subdivision requirements, procedures, and standards from the UDO.

    Covers: subdivision types (exempt, minor, major), approval processes,
    plat requirements, improvement standards, and performance guarantees.

    Use this tool for questions about:
    - Subdividing land, lot splits, parcel divisions
    - Minor vs major subdivision thresholds
    - Plat approval process and requirements
    - Required improvements (streets, sidewalks, utilities, open space)
    - Performance guarantees and HOA covenants

    Args:
        query: What to look up. Use keywords like "minor", "major", "exempt",
               "plat", "process", "improvements", "streets", "open space",
               "stormwater", "performance guarantee", "lot split", or "all"
               for a complete overview.
    """
    subdiv = _load_subdivision_index()
    q = query.lower().strip()
    sections = []

    # Aliases for common queries
    aliases = {
        "lot split": ["types"],
        "parcel division": ["types"],
        "two-lot": ["types"],
        "2-lot": ["types"],
        "plat": ["plat_requirements", "general_procedures"],
        "plat approval": ["plat_requirements", "general_procedures"],
        "process": ["types"],
        "approval": ["types", "general_procedures"],
        "improvements": ["improvement_requirements"],
        "infrastructure": ["improvement_requirements"],
        "guarantee": ["improvement_requirements"],
        "performance": ["improvement_requirements"],
    }

    # Determine which sections to return
    show_types = False
    show_improvements = False
    show_plats = False
    show_procedures = False

    if q == "all" or q == "overview":
        show_types = show_improvements = show_plats = show_procedures = True
    else:
        # Check aliases first
        for alias, targets in aliases.items():
            if alias in q:
                if "types" in targets:
                    show_types = True
                if "improvement_requirements" in targets:
                    show_improvements = True
                if "plat_requirements" in targets:
                    show_plats = True
                if "general_procedures" in targets:
                    show_procedures = True

        # Check for specific type keywords
        for type_key in subdiv["types"]:
            if type_key in q or subdiv["types"][type_key]["name"].lower() in q:
                show_types = True

        # Check for specific improvement keywords
        for imp_key, imp_data in subdiv["improvement_requirements"].items():
            searchable = f"{imp_key} {imp_data['summary']}".lower()
            if any(word in searchable for word in q.split()):
                show_improvements = True

        # Check for plat-related keywords
        if any(w in q for w in ["plat", "sketch", "preliminary", "engineering", "drawing", "final plat"]):
            show_plats = True

        # Check for procedure keywords
        if any(w in q for w in ["procedure", "permit", "zoning permit", "open space subdivision"]):
            show_procedures = True

        # If nothing matched, show types as default (most common query)
        if not any([show_types, show_improvements, show_plats, show_procedures]):
            show_types = True

    # --- Build output ---

    if show_types:
        lines = ["# Subdivision Types\n"]
        for type_key, t in subdiv["types"].items():
            lines.append(f"## {t['name']} (Section {t['section']})")
            lines.append(f"{t['summary']}\n")

            criteria_label = "Criteria (ALL required):" if t.get("all_criteria_required") else \
                             "Criteria (ANY triggers this type):" if t.get("any_criteria_triggers") else "Criteria:"
            lines.append(f"**{criteria_label}**")
            for c in t["criteria"]:
                lines.append(f"- {c}")

            lines.append(f"\n**Approval Authority:** {t['approval_authority']}")

            if t.get("preliminary_plat_required") is not None:
                lines.append(f"**Preliminary Plat Required:** {'Yes' if t['preliminary_plat_required'] else 'No'}")

            if t.get("engineering_drawings"):
                lines.append(f"**Engineering Drawings:** {t['engineering_drawings']}")

            if t.get("process_steps"):
                lines.append(f"\n**Process (Section {t['process_section']}):**")
                for i, step in enumerate(t["process_steps"], 1):
                    lines.append(f"{i}. {step}")

            if t.get("preliminary_plat_validity"):
                lines.append(f"\n**Preliminary Plat Validity:** {t['preliminary_plat_validity']}")
            lines.append("")

        sections.append("\n".join(lines))

    if show_improvements:
        lines = ["# Improvement Requirements\n"]
        for imp_key, imp in subdiv["improvement_requirements"].items():
            # Filter by query if specific improvement requested
            if q != "all" and q != "overview" and q != "improvements" and q != "infrastructure":
                searchable = f"{imp_key} {imp['summary']}".lower()
                if not any(word in searchable for word in q.split()):
                    continue

            title = imp_key.replace("_", " ").title()
            lines.append(f"## {title} (Section {imp['section']})")
            lines.append(imp["summary"])
            if imp.get("detail_sections"):
                lines.append(f"*See also: {', '.join(f'Section {s}' for s in imp['detail_sections'])}*")
            lines.append("")
        sections.append("\n".join(lines))

    if show_plats:
        lines = ["# Plat Requirements\n"]
        for plat_key, plat in subdiv["plat_requirements"].items():
            title = plat_key.replace("_", " ").title()
            lines.append(f"## {title} (Section {plat['section']})")
            if plat.get("required_for"):
                lines.append(f"**Required for:** {plat['required_for']}")
            lines.append(plat["summary"])
            lines.append("")
        sections.append("\n".join(lines))

    if show_procedures:
        lines = ["# General Procedures\n"]
        for proc_key, proc in subdiv["general_procedures"].items():
            title = proc_key.replace("_", " ").title()
            lines.append(f"## {title} (Section {proc['section']})")
            lines.append(proc["summary"])
            lines.append("")
        sections.append("\n".join(lines))

    return "\n\n---\n\n".join(sections)


@mcp.tool()
def search_ordinance(query: str) -> str:
    """Search across the entire China Grove Unified Development Ordinance.

    Searches permitted uses, districts, special requirements, subdivision
    procedures, and all 18 chapter markdown files for matching content.
    Supports multi-word queries (all words must appear in a match).

    Args:
        query: Search term or phrase (e.g., "setback", "microbrewery", "flood",
               "subdivision minimum lot size", "lot split parcel division").
    """
    q = query.lower()
    words = q.split()
    sections = []

    def _matches_text(text: str) -> bool:
        """Check if all query words appear in the text."""
        text_lower = text.lower()
        return all(w in text_lower for w in words)

    def _any_word_matches(text: str) -> bool:
        """Check if any query word appears in the text."""
        text_lower = text.lower()
        return any(w in text_lower for w in words)

    # Search permitted uses
    uses = _load_permitted_uses()
    use_matches = [u for u in uses if _any_word_matches(
        f"{u['use']} {u.get('category', '')} {u.get('special_requirements', '')}"
    )]
    if use_matches:
        lines = [f"## Permitted Uses ({len(use_matches)} matches)\n"]
        for u in use_matches[:15]:
            districts_str = ", ".join(
                f"{d}({u['districts'][d]})" for d in DISTRICT_ORDER if u["districts"].get(d)
            )
            lines.append(f"- **{u['use']}** [{u['category']}]: {districts_str}")
        if len(use_matches) > 15:
            lines.append(f"- ... and {len(use_matches) - 15} more")
        sections.append("\n".join(lines))

    # Search districts
    districts = _load_districts()
    for code, info in districts.items():
        searchable = f"{info['name']} {info['intent']} {info['character']} {' '.join(info.get('key_rules', []))}"
        if _any_word_matches(searchable):
            sections.append(f"## District: {code} — {info['name']}\n{info['intent']}")

    # Search special requirements
    index = _load_special_requirements_index()
    sr_matches = [s for s in index if _any_word_matches(f"{s['title']} {s['summary']}")]
    if sr_matches:
        lines = [f"## Special Requirements ({len(sr_matches)} matches)\n"]
        for s in sr_matches:
            lines.append(f"- **Section {s['section']}:** {s['title']}")
            lines.append(f"  {s['summary'][:150]}...")
        sections.append("\n".join(lines))

    # Search subdivision index
    subdiv = _load_subdivision_index()
    subdiv_matches = []

    # Search subdivision types
    for type_key, t in subdiv["types"].items():
        searchable = f"{t['name']} {t['summary']} {' '.join(t['criteria'])} {t['approval_authority']}"
        if t.get("process_steps"):
            searchable += " " + " ".join(t["process_steps"])
        if _any_word_matches(searchable):
            subdiv_matches.append(f"- **{t['name']}** (Section {t['section']}): {t['summary']}")

    # Search improvement requirements
    for imp_key, imp in subdiv["improvement_requirements"].items():
        searchable = f"{imp_key} {imp['summary']}"
        if _any_word_matches(searchable):
            title = imp_key.replace("_", " ").title()
            subdiv_matches.append(f"- **{title}** (Section {imp['section']}): {imp['summary'][:120]}...")

    # Search plat requirements
    for plat_key, plat in subdiv["plat_requirements"].items():
        searchable = f"{plat_key} {plat['summary']} {plat.get('required_for', '')}"
        if _any_word_matches(searchable):
            title = plat_key.replace("_", " ").title()
            subdiv_matches.append(f"- **{title}** (Section {plat['section']}): {plat['summary'][:120]}...")

    if subdiv_matches:
        lines = [f"## Subdivision Procedures ({len(subdiv_matches)} matches)\n"]
        lines.extend(subdiv_matches)
        lines.append("\n*Use get_subdivision_requirements() for full details.*")
        sections.append("\n".join(lines))

    # Search general provisions index
    gp_index = _load_general_provisions_index()
    gp_matches = [s for s in gp_index if _any_word_matches(f"{s['title']} {s['summary']}")]
    if gp_matches:
        lines = [f"## General Provisions ({len(gp_matches)} matches)\n"]
        for s in gp_matches:
            lines.append(f"- **Section {s['section']}:** {s['title']}")
            lines.append(f"  {s['summary'][:200]}")
        lines.append("\n*Use get_general_provisions() for full text.*")
        sections.append("\n".join(lines))

    # Search markdown files with paragraph context (5-line window)
    md_matches = []
    if MARKDOWN_DIR.is_dir():
        for md_file in sorted(MARKDOWN_DIR.glob("*.md")):
            with open(md_file) as f:
                file_lines = f.readlines()
            # Use 5-line sliding window to match multi-line provisions
            for i in range(len(file_lines)):
                window_start = max(0, i - 2)
                window_end = min(len(file_lines), i + 3)
                window = " ".join(file_lines[window_start:window_end])
                if _matches_text(window):
                    # Build context snippet: the paragraph around the match
                    snippet_start = max(0, i - 2)
                    snippet_end = min(len(file_lines), i + 3)
                    snippet = " ".join(
                        line.strip() for line in file_lines[snippet_start:snippet_end]
                        if line.strip()
                    )
                    md_matches.append((md_file.name, i + 1, snippet[:300]))

    if md_matches:
        # Deduplicate by file and proximity (collapse matches within 5 lines)
        unique_matches = []
        seen_ranges = {}
        for fname, lineno, text in md_matches:
            key = fname
            if key in seen_ranges:
                # Skip if within 5 lines of a previous match in same file
                if any(abs(lineno - prev) < 5 for prev in seen_ranges[key]):
                    continue
                seen_ranges[key].append(lineno)
            else:
                seen_ranges[key] = [lineno]
            unique_matches.append((fname, lineno, text))
        md_matches = unique_matches

        lines = [f"## Ordinance Text ({len(md_matches)} matches)\n"]
        for fname, lineno, text in md_matches[:20]:
            lines.append(f"- **{fname}:{lineno}:**\n  {text}")
        if len(md_matches) > 20:
            lines.append(f"- ... and {len(md_matches) - 20} more matches")
        sections.append("\n".join(lines))

    if not sections:
        return f"No results found for '{query}'."

    return "\n\n---\n\n".join(sections)


@mcp.tool()
def list_districts() -> str:
    """List all zoning district codes with their names. Useful as a quick reference."""
    districts = _load_districts()
    lines = ["# China Grove Zoning Districts\n"]
    lines.append("| Code | Name | Character |")
    lines.append("|------|------|-----------|")
    for code, info in districts.items():
        lines.append(f"| {code} | {info['name']} | {info['character']} |")
    return "\n".join(lines)


@mcp.tool()
def can_i_build(use: str, district: str) -> str:
    """Answer the question: 'Can I build/operate X in district Y?'

    Provides a complete answer including permission status, dimensional standards,
    and any special requirements that apply.

    Args:
        use: What the user wants to build or operate (e.g., "restaurant", "duplex").
        district: The zoning district code (e.g., "C-B", "R-S").
    """
    d = district.upper()
    if d not in DISTRICT_ORDER:
        return f"Unknown district '{district}'. Valid: {', '.join(DISTRICT_ORDER)}"

    uses = _load_permitted_uses()
    q = use.lower()
    matches = [u for u in uses if q in u["use"].lower()]

    if not matches:
        matches = [u for u in uses if q in u.get("category", "").lower()]

    if not matches:
        return (
            f"No use matching '{use}' found in the permitted uses table. "
            "Try a different search term, or use search_ordinance() for a broader search."
        )

    results = []
    for u in matches:
        perm = u["districts"].get(d)
        lines = [f"## {u['use']} in {d}\n"]

        if perm:
            lines.append(f"**Status: {PERMISSION_LABELS.get(perm, perm)}** ({perm})\n")
            if perm == "X":
                lines.append("This use is permitted by right with administrative review and approval, "
                             "subject to district provisions and other applicable requirements.")
            elif perm == "C":
                lines.append("This use requires Conditional Zoning approval. The request is processed "
                             "per Section 15.6 and requires Town Council approval with conditions "
                             "mutually agreed upon by Council and petitioner.")
            elif perm == "S":
                lines.append("This use requires a Special Use Permit. Requires Planning Board review "
                             "and recommendation, then Town Council review and approval, subject to "
                             "district provisions, applicable requirements, and conditions of approval.")
        else:
            lines.append("**Status: NOT PERMITTED**\n")
            lines.append(f"This use is not allowed in the {d} district.")
            # Suggest where it IS permitted
            permitted_in = [
                f"{dd} ({u['districts'][dd]} — {PERMISSION_LABELS.get(u['districts'][dd], '')})"
                for dd in DISTRICT_ORDER if u["districts"].get(dd)
            ]
            if permitted_in:
                lines.append(f"\nThis use IS permitted in: {', '.join(permitted_in)}")
            results.append("\n".join(lines))
            continue

        # Add special requirements
        if u.get("special_requirements"):
            sr_sections = u["special_requirements"].split(",")
            index = _load_special_requirements_index()
            for sr in sr_sections:
                sr = sr.strip()
                sr_match = next((s for s in index if s["section"] == sr), None)
                if sr_match:
                    lines.append(f"\n### Special Requirements (Section {sr}: {sr_match['title']})")
                    lines.append(sr_match["summary"])

        # Add discrepancy note if present
        if u.get("discrepancy_note"):
            lines.append(f"\n### ⚠ Ordinance Discrepancy")
            lines.append(u["discrepancy_note"])

        # Add dimensional standards
        standards = _load_dimensional_standards()
        principal = [s for s in standards["principal_structures"] if s["district"] == d]
        if principal:
            lines.append(f"\n### Dimensional Standards for {d}")
            for entry in principal:
                lines.append(f"\n**{entry['use_type']}:**")
                if entry.get("density"):
                    lines.append(f"- Density: {entry['density']}")
                if entry.get("min_lot_size"):
                    lines.append(f"- Min Lot Size: {entry['min_lot_size']}")
                front = entry.get("setback_front_ft")
                front_max = entry.get("setback_front_max_ft")
                if front is not None:
                    lines.append(f"- Front Setback: {front}ft" + (f" (max {front_max}ft)" if front_max else ""))
                side_note = entry.get("setback_side_interior_note")
                side_min = entry.get("setback_side_min_ft")
                if side_min is not None:
                    lines.append(f"- Side Setback: {side_note if side_note else f'{side_min}ft'}")
                if entry.get("setback_rear_ft") is not None:
                    lines.append(f"- Rear Setback: {entry['setback_rear_ft']}ft")
                if entry.get("max_height_ft") is not None:
                    lines.append(f"- Max Height: {entry['max_height_ft']}ft")

        results.append("\n".join(lines))

    return "\n\n---\n\n".join(results)


# --- ArcGIS Parcel / Zoning Lookup ---

_ARCGIS_BASE = "https://services5.arcgis.com/3sjPFdTeOMIkNjsx/arcgis/rest/services"
_PARCEL_URL = f"{_ARCGIS_BASE}/parcels/FeatureServer/0/query"
_ZONING_URL = f"{_ARCGIS_BASE}/Town_Zoning/FeatureServer/3/query"
_ETJ_URL = f"{_ARCGIS_BASE}/Town_Zoning/FeatureServer/2/query"
_CORP_LIMITS_URL = f"{_ARCGIS_BASE}/Town_Zoning/FeatureServer/0/query"

_PARCEL_OUT_FIELDS = (
    "PIN,PARCEL_ID,OWNNAME,OWN2,PROP_ADDRE,"
    "DEEDACRE,CALCACRE,TAX_DISTRI,PARENT_PIN,"
    "TOT_VAL,LANDFMV,IMP_FMV"
)


def _arcgis_query(url: str, params: dict) -> dict:
    """Execute an ArcGIS REST query and return the JSON response."""
    params.setdefault("f", "json")
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _centroid_from_rings(rings: list) -> tuple[float, float]:
    """Compute a simple centroid from polygon rings (first ring only)."""
    ring = rings[0]
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _fmt_currency(val) -> str:
    """Format a number as currency."""
    if val is None:
        return "N/A"
    try:
        return f"${val:,.0f}"
    except (TypeError, ValueError):
        return str(val)


# GIS often returns district codes without hyphens (e.g. "RS" instead of "R-S").
# Normalize so downstream tools (get_district_info, can_i_build, etc.) work.
_UNHYPHENATED_TO_CANONICAL = {}
for _code in DISTRICT_ORDER:
    _UNHYPHENATED_TO_CANONICAL[_code.replace("-", "").upper()] = _code


def _normalize_district_code(raw: str) -> str:
    """Map a raw GIS zoning code to its canonical hyphenated form."""
    upper = raw.strip().upper()
    # Already canonical?
    if upper in DISTRICT_ORDER:
        return upper
    # Known unhyphenated variant?
    if upper in _UNHYPHENATED_TO_CANONICAL:
        return _UNHYPHENATED_TO_CANONICAL[upper]
    # Unknown — return as-is (caller will show it with a note)
    return raw


def _spatial_query(url: str, x: float, y: float, out_fields: str = "*") -> dict | None:
    """Run a point-in-polygon spatial query against an ArcGIS layer."""
    params = {
        "geometry": json.dumps({"x": x, "y": y}),
        "geometryType": "esriGeometryPoint",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "102719",
        "outFields": out_fields,
        "returnGeometry": "false",
        "f": "json",
    }
    result = _arcgis_query(url, params)
    features = result.get("features", [])
    return features[0]["attributes"] if features else None


@mcp.tool()
def get_parcel_info(
    pin: str | None = None,
    address: str | None = None,
    owner: str | None = None,
) -> str:
    """Look up a parcel and its zoning from Rowan County GIS.

    Accepts ONE of pin, address, or owner. Returns parcel details, zoning
    district, and jurisdiction status. The returned district code can be
    passed directly to get_district_info(), get_dimensional_standards(),
    or can_i_build().

    Args:
        pin: Parcel Identification Number (e.g., "5626-01-38-0952").
        address: Property address to search (partial match, e.g., "1735 W NC 152 HWY").
        owner: Owner name to search (partial match, e.g., "BULLARD").
    """
    # --- Validate input ---
    provided = sum(1 for v in (pin, address, owner) if v)
    if provided == 0:
        return "Please provide one of: pin, address, or owner."
    if provided > 1:
        return "Please provide only ONE of: pin, address, or owner."

    # --- Step 1: Query parcel layer ---
    if pin:
        pin_val = pin.strip()
        where = f"PIN = '{pin_val}'"
        result = _arcgis_query(_PARCEL_URL, {
            "where": where,
            "outFields": _PARCEL_OUT_FIELDS,
            "returnGeometry": "true",
            "outSR": "102719",
        })
        features = result.get("features", [])
        # Try PARCEL_ID if PIN returned nothing
        if not features:
            where = f"PARCEL_ID = '{pin_val}'"
            result = _arcgis_query(_PARCEL_URL, {
                "where": where,
                "outFields": _PARCEL_OUT_FIELDS,
                "returnGeometry": "true",
                "outSR": "102719",
            })
            features = result.get("features", [])
        if not features:
            return (
                f"No parcel found for PIN/PARCEL_ID '{pin_val}'. "
                "Check the format (e.g., '5626-01-38-0952') and try again."
            )
    elif address:
        addr_val = address.strip().upper()
        where = f"PROP_ADDRE LIKE '%{addr_val}%'"
        result = _arcgis_query(_PARCEL_URL, {
            "where": where,
            "outFields": _PARCEL_OUT_FIELDS,
            "returnGeometry": "true",
            "outSR": "102719",
        })
        features = result.get("features", [])
        if not features:
            return f"No parcels found matching address '{address}'."
    else:
        owner_val = owner.strip().upper()
        where = f"OWNNAME LIKE '%{owner_val}%' OR OWN2 LIKE '%{owner_val}%'"
        result = _arcgis_query(_PARCEL_URL, {
            "where": where,
            "outFields": _PARCEL_OUT_FIELDS,
            "returnGeometry": "true",
            "outSR": "102719",
        })
        features = result.get("features", [])
        if not features:
            return f"No parcels found matching owner '{owner}'."

    # --- Multiple matches: return selection list ---
    if len(features) > 1:
        lines = [f"**{len(features)} parcels found.** Please requery with a specific PIN:\n"]
        for f_ in features[:25]:
            a = f_["attributes"]
            lines.append(
                f"- **{a.get('PIN', 'N/A')}** — {a.get('PROP_ADDRE', 'N/A')} "
                f"(Owner: {a.get('OWNNAME', 'N/A')})"
            )
        if len(features) > 25:
            lines.append(f"- ... and {len(features) - 25} more")
        return "\n".join(lines)

    # --- Single match: extract parcel attributes ---
    feature = features[0]
    attr = feature["attributes"]
    geom = feature.get("geometry", {})

    # --- Step 2: Compute centroid and run spatial queries in parallel ---
    rings = geom.get("rings")
    if not rings:
        return (
            f"Parcel {attr.get('PIN')} found but no geometry returned. "
            "Cannot determine zoning or jurisdiction."
        )

    cx, cy = _centroid_from_rings(rings)

    zoning_result = None
    etj_result = None
    corp_result = None
    errors = []

    def _query_zoning():
        return _spatial_query(
            _ZONING_URL, cx, cy,
            "zoning,effective_date"
        )

    def _query_etj():
        return _spatial_query(_ETJ_URL, cx, cy, "*")

    def _query_corp():
        return _spatial_query(_CORP_LIMITS_URL, cx, cy, "CITY_NAME")

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_query_zoning): "zoning",
            executor.submit(_query_etj): "etj",
            executor.submit(_query_corp): "corporate_limits",
        }
        for future in as_completed(futures):
            layer_name = futures[future]
            try:
                result_data = future.result()
                if layer_name == "zoning":
                    zoning_result = result_data
                elif layer_name == "etj":
                    etj_result = result_data
                else:
                    corp_result = result_data
            except Exception as e:
                errors.append(f"{layer_name}: {e}")

    # --- Format output ---
    lines = []

    # Parcel info
    pin_val = attr.get("PIN", "N/A")
    lines.append(f"**PARCEL:** {pin_val}")
    owner_line = attr.get("OWNNAME", "N/A")
    own2 = attr.get("OWN2")
    if own2:
        owner_line += f" / {own2}"
    lines.append(f"**Owner:** {owner_line}")
    lines.append(f"**Property Address:** {attr.get('PROP_ADDRE', 'N/A')}")

    deed_acres = attr.get("DEEDACRE")
    calc_acres = attr.get("CALCACRE")
    acres_line = f"**Deed Acres:** {deed_acres} | **Calculated Acres:** {calc_acres}"
    if deed_acres is not None and calc_acres is not None:
        try:
            if abs(float(deed_acres) - float(calc_acres)) > 0.1:
                acres_line += "\n  ⚠ Deed and calculated acreage differ by more than 0.1 acres"
        except (TypeError, ValueError):
            pass
    lines.append(acres_line)

    lines.append(
        f"**Total Value:** {_fmt_currency(attr.get('TOT_VAL'))} "
        f"(Land: {_fmt_currency(attr.get('LANDFMV'))} / "
        f"Improvements: {_fmt_currency(attr.get('IMP_FMV'))})"
    )

    parent_pin = attr.get("PARENT_PIN")
    if parent_pin:
        lines.append(f"**Parent PIN:** {parent_pin}")
        lines.append(
            "  ⚠ Previously split — verify 10-year lookback for "
            "Recordation-Only subdivision eligibility"
        )

    lines.append("")

    # Zoning info — normalize code so it chains to downstream tools
    if zoning_result:
        raw_zoning = zoning_result.get("zoning", "N/A")
        zoning_code = _normalize_district_code(raw_zoning) if raw_zoning != "N/A" else raw_zoning
        lines.append(f"**ZONING:** {zoning_code}")
        # NOTE: The GIS conditional_use field is a district-level attribute
        # (e.g. " yes" on the R-S polygon) indicating some uses in that
        # district require conditional zoning — it is NOT a parcel-level
        # CUP flag.  We intentionally omit it to avoid false positives.
        # Parcel-level CUP status is only available from Town records.
        eff_date = zoning_result.get("effective_date")
        if eff_date:
            lines.append(f"**Zoning Effective:** {eff_date}")
    elif "zoning" in [e.split(":")[0] for e in errors]:
        lines.append("**ZONING:** ⚠ Zoning layer query failed")
    else:
        lines.append(
            "**ZONING:** No zoning district found — "
            "parcel may be unzoned or at jurisdiction boundary"
        )

    # Jurisdiction
    is_etj = False
    if corp_result:
        city = corp_result.get("CITY_NAME", "China Grove")
        lines.append(f"**Jurisdiction:** Inside corporate limits ({city})")
    elif etj_result:
        is_etj = True
        lines.append("**Jurisdiction:** ETJ (Extra-Territorial Jurisdiction)")
    elif "corporate_limits" in [e.split(":")[0] for e in errors] and \
         "etj" in [e.split(":")[0] for e in errors]:
        lines.append("**Jurisdiction:** ⚠ Could not determine — both jurisdiction layers failed")
    else:
        lines.append("**Jurisdiction:** Outside jurisdiction")

    # ETJ advisory — procedural differences affect applicants
    if is_etj:
        lines.append("")
        lines.append(
            "⚠ **ETJ Advisory:** This property is within China Grove's "
            "Extra-Territorial Jurisdiction. The Town's UDO applies for zoning "
            "and subdivision review, but building inspections are conducted by "
            "Rowan County, not the Town. Confirm with the Zoning Administrator "
            "which approval steps require Town vs. County action before "
            "submitting any applications."
        )

    # Layer errors
    if errors:
        lines.append("")
        lines.append("**⚠ Partial results — some layers failed:**")
        for err in errors:
            lines.append(f"- {err}")

    # Cross-reference hint
    if zoning_result and zoning_result.get("zoning"):
        zc = zoning_code
        lines.append("")
        lines.append(
            f"→ District code **{zc}** can be passed directly to: "
            "get_district_info(), get_dimensional_standards(), can_i_build()"
        )

    return "\n".join(lines)


# --- Infill Context (GIS-based neighbor lookup) ---


@mcp.tool()
def get_infill_context(pin: str) -> str:
    """Find neighboring parcels within 300 ft for infill setback averaging (Section 2.2D).

    Looks up the subject parcel, then queries for nearby parcels on the same street
    and in the same zoning district within 300 feet. Returns the list of neighboring
    parcels that would be used for setback averaging.

    NOTE: Actual structure setback distances are NOT available from GIS parcel data —
    a site survey or building footprint data is needed. This tool identifies which
    parcels fall within the 300-ft radius to narrow the field work.

    Args:
        pin: Parcel Identification Number (e.g., "5626-01-38-0952").
    """
    pin_val = pin.strip()

    # Step 1: Get the subject parcel with geometry
    result = _arcgis_query(_PARCEL_URL, {
        "where": f"PIN = '{pin_val}'",
        "outFields": f"{_PARCEL_OUT_FIELDS}",
        "returnGeometry": "true",
        "outSR": "102719",
    })
    features = result.get("features", [])

    # Try PARCEL_ID if PIN didn't work
    if not features:
        result = _arcgis_query(_PARCEL_URL, {
            "where": f"PARCEL_ID = '{pin_val}'",
            "outFields": f"{_PARCEL_OUT_FIELDS}",
            "returnGeometry": "true",
            "outSR": "102719",
        })
        features = result.get("features", [])

    if not features:
        return f"No parcel found for PIN '{pin_val}'."

    if len(features) > 1:
        return f"Multiple parcels match '{pin_val}'. Please use a more specific PIN."

    feature = features[0]
    attr = feature["attributes"]
    geom = feature.get("geometry", {})
    rings = geom.get("rings")

    if not rings:
        return f"Parcel {pin_val} found but no geometry — cannot compute neighbors."

    cx, cy = _centroid_from_rings(rings)
    subject_address = attr.get("PROP_ADDRE", "")

    # Step 2: Get zoning for the subject parcel
    zoning_result = _spatial_query(_ZONING_URL, cx, cy, "zoning")
    if not zoning_result:
        return (
            f"Parcel {pin_val} found but could not determine zoning district. "
            "Cannot identify infill context without knowing the zoning district."
        )

    raw_zoning = zoning_result.get("zoning", "")
    subject_zoning = _normalize_district_code(raw_zoning) if raw_zoning else "Unknown"

    # Step 3: Buffer query — find parcels within 300 ft
    # Build a buffer geometry around the centroid (300 ft radius)
    buffer_params = {
        "geometry": json.dumps({"x": cx, "y": cy}),
        "geometryType": "esriGeometryPoint",
        "spatialRel": "esriSpatialRelIntersects",
        "distance": 300,
        "units": "esriSRUnit_Foot",
        "inSR": "102719",
        "outFields": _PARCEL_OUT_FIELDS,
        "returnGeometry": "true",
        "outSR": "102719",
        "where": "1=1",
    }

    try:
        neighbor_result = _arcgis_query(_PARCEL_URL, buffer_params)
    except Exception as e:
        return f"Buffer query failed: {e}"

    neighbor_features = neighbor_result.get("features", [])

    # Step 4: Filter neighbors — same street, same zoning, exclude subject
    lines = [
        f"# Infill Context for {pin_val}",
        f"**Subject Address:** {subject_address}",
        f"**Subject Zoning:** {subject_zoning}",
        f"**Search Radius:** 300 feet from parcel centroid\n",
    ]

    # Extract street name from subject address for matching
    subject_street = ""
    if subject_address:
        # Remove house number to get street name
        parts = subject_address.split()
        if len(parts) > 1:
            # Skip leading numbers
            street_parts = []
            for p in parts:
                if not p.isdigit() and not street_parts:
                    street_parts.append(p)
                elif street_parts:
                    street_parts.append(p)
            subject_street = " ".join(street_parts).upper()

    same_street_same_zone = []
    same_zone_other_street = []

    for nf in neighbor_features:
        na = nf["attributes"]
        n_pin = na.get("PIN", "")

        # Skip subject parcel
        if n_pin == pin_val:
            continue

        n_address = na.get("PROP_ADDRE", "")

        # Get neighbor's zoning
        n_geom = nf.get("geometry", {})
        n_rings = n_geom.get("rings")
        n_zoning = "Unknown"
        if n_rings:
            ncx, ncy = _centroid_from_rings(n_rings)
            n_zone_result = _spatial_query(_ZONING_URL, ncx, ncy, "zoning")
            if n_zone_result:
                raw = n_zone_result.get("zoning", "")
                n_zoning = _normalize_district_code(raw) if raw else "Unknown"

        # Check if same zoning district
        if n_zoning != subject_zoning:
            continue

        # Check if same street
        on_same_street = False
        if subject_street and n_address:
            n_street = ""
            n_parts = n_address.split()
            if len(n_parts) > 1:
                n_street_parts = []
                for p in n_parts:
                    if not p.isdigit() and not n_street_parts:
                        n_street_parts.append(p)
                    elif n_street_parts:
                        n_street_parts.append(p)
                n_street = " ".join(n_street_parts).upper()
            if n_street and n_street == subject_street:
                on_same_street = True

        entry = f"- **{n_pin}** — {n_address} (Zoning: {n_zoning})"

        if on_same_street:
            same_street_same_zone.append(entry)
        else:
            same_zone_other_street.append(entry)

    if same_street_same_zone:
        lines.append(f"## Same Street & Same Zoning District ({len(same_street_same_zone)} parcels)\n")
        lines.append("These parcels are most likely relevant for the Section 2.2D setback average:\n")
        lines.extend(same_street_same_zone)
    else:
        lines.append("## Same Street & Same Zoning District\n")
        lines.append("No neighboring parcels found on the same street within 300 ft in the same district.")

    if same_zone_other_street:
        lines.append(f"\n## Same Zoning District, Different Street ({len(same_zone_other_street)} parcels)\n")
        lines.extend(same_zone_other_street)

    lines.append("\n---")
    lines.append("## Section 2.2D Infill Setback Rule")
    lines.append(
        "Front and side yard setbacks for infill lot development shall be equal to the "
        "average for similar principal structures on the same side of the street and within "
        "the same zoning district within 300 feet of either side of the lot in question."
    )
    lines.append(
        "\n**⚠ Note:** This tool identifies parcels within the 300-ft radius. Actual "
        "structure setback measurements require building footprint data, site plans, "
        "or field survey. The Zoning Administrator determines the applicable setback average."
    )

    return "\n".join(lines)


# --- NCGS Chapter 160D (State Zoning Law) ---

_160D_ARTICLE_MAP = {
    "1": "Article-01-General-Provisions.md",
    "2": "Article-02-Planning-Jurisdiction.md",
    "3": "Article-03-Boards.md",
    "4": "Article-04-Administration-Enforcement-Appeals.md",
    "5": "Article-05-Planning.md",
    "6": "Article-06-Development-Regulation.md",
    "7": "Article-07-Zoning-Regulation.md",
    "8": "Article-08-Subdivision-Regulation.md",
    "9": "Article-09-Particular-Uses-and-Areas.md",
    "10": "Article-10-Development-Agreements.md",
    "11": "Article-11-Building-Code-Enforcement.md",
    "12": "Article-12-Minimum-Housing-Codes.md",
    "13": "Article-13-Additional-Authority.md",
    "14": "Article-14-Judicial-Review.md",
}


def _find_160d_section(section_num: str) -> str | None:
    """Find and return the text of a specific 160D section from the article files."""
    # Determine which article file to search based on section number
    # 160D-1xx = Art 1, 160D-2xx = Art 2, ..., 160D-14xx = Art 14
    num_part = section_num.replace("160D-", "").replace("160d-", "")

    # Map section number to article
    article_num = None
    try:
        sec_int = int(re.match(r"(\d+)", num_part).group(1))
        if sec_int < 200:
            article_num = "1"
        elif sec_int < 300:
            article_num = "2"
        elif sec_int < 400:
            article_num = "3"
        elif sec_int < 500:
            article_num = "4"
        elif sec_int < 600:
            article_num = "5"
        elif sec_int < 700:
            article_num = "6"
        elif sec_int < 800:
            article_num = "7"
        elif sec_int < 900:
            article_num = "8"
        elif sec_int < 1000:
            article_num = "9"
        elif sec_int < 1100:
            article_num = "10"
        elif sec_int < 1200:
            article_num = "11"
        elif sec_int < 1300:
            article_num = "12"
        elif sec_int < 1400:
            article_num = "13"
        else:
            article_num = "14"
    except (AttributeError, ValueError):
        return None

    filename = _160D_ARTICLE_MAP.get(article_num)
    if not filename:
        return None

    filepath = STATUTES_DIR / filename
    if not filepath.exists():
        return None

    with open(filepath) as f:
        content = f.read()

    # Normalize the search target — try with and without "160D-" prefix
    targets = [f"160D-{num_part}", f"§ 160D-{num_part}"]

    # Find the section header and extract until the next section header
    for target in targets:
        pattern = re.compile(
            rf"^##\s+.*{re.escape(target)}.*$",
            re.MULTILINE,
        )
        match = pattern.search(content)
        if match:
            start = match.start()
            # Find the next ## header
            next_header = re.search(r"^## ", content[match.end():], re.MULTILINE)
            if next_header:
                end = match.end() + next_header.start()
            else:
                end = len(content)
            return content[start:end].strip()

    return None


@mcp.tool()
def get_160d_section(section: str) -> str:
    """Get the text of a specific section of NCGS Chapter 160D (NC zoning enabling statute).

    Chapter 160D is the state law that grants and governs local zoning authority
    in North Carolina. When the local UDO conflicts with 160D, state law controls.

    Args:
        section: Section number, e.g. "160D-702", "702", "108.1", "160D-403".
    """
    # Normalize input
    cleaned = section.strip()
    if not cleaned.upper().startswith("160D-"):
        cleaned = f"160D-{cleaned}"
    num_part = cleaned.replace("160D-", "").replace("160d-", "")

    result = _find_160d_section(num_part)
    if result:
        return result

    return (
        f"Section {cleaned} not found. Use search_160d() to search by keyword, "
        "or try the full section number (e.g., '160D-702', '160D-108.1')."
    )


@mcp.tool()
def search_160d(query: str) -> str:
    """Search NCGS Chapter 160D for keywords or phrases.

    Chapter 160D is the state law that grants and governs local zoning authority
    in North Carolina. Use this to find relevant state law provisions, especially
    when the local UDO may conflict with or be supplemented by state requirements.

    Args:
        query: Search term or phrase (e.g., "vested rights", "extraterritorial",
               "board of adjustment", "variance", "quasi-judicial").
    """
    words = query.lower().split()
    matches = []

    if not STATUTES_DIR.is_dir():
        return "160D statute files not found. Expected in statutes/ directory."

    for md_file in sorted(STATUTES_DIR.glob("*.md")):
        with open(md_file) as f:
            file_lines = f.readlines()

        for i, line in enumerate(file_lines, 1):
            if all(w in line.lower() for w in words):
                matches.append((md_file.stem, i, line.strip()[:150]))

    # If single-line matching found nothing with multi-word, try 3-line windows
    if not matches and len(words) > 1:
        for md_file in sorted(STATUTES_DIR.glob("*.md")):
            with open(md_file) as f:
                file_lines = f.readlines()
            for i in range(len(file_lines)):
                window = " ".join(file_lines[max(0, i - 1):i + 2])
                if all(w in window.lower() for w in words):
                    matches.append((md_file.stem, i + 1, file_lines[i].strip()[:150]))

    if not matches:
        return f"No results found in NCGS 160D for '{query}'."

    # Deduplicate
    seen = set()
    unique = []
    for m in matches:
        key = (m[0], m[1])
        if key not in seen:
            seen.add(key)
            unique.append(m)
    matches = unique

    lines = [f"## NCGS 160D — {len(matches)} matches for '{query}'\n"]
    for fname, lineno, text in matches[:30]:
        lines.append(f"- **{fname}:{lineno}:** {text}")
    if len(matches) > 30:
        lines.append(f"- ... and {len(matches) - 30} more matches")
    lines.append("\n*Use get_160d_section() to read the full text of a specific section.*")

    return "\n".join(lines)
