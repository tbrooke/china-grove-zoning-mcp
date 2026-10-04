# China Grove Zoning MCP Server

An [MCP](https://modelcontextprotocol.io/) server that provides AI-powered zoning and land use research tools for the Town of China Grove, NC. It draws from the Unified Development Ordinance (UDO), structured data files, and Rowan County GIS to answer questions about what can be built where and why.

## Tools

| Tool | Description |
|------|-------------|
| `lookup_permitted_use` | Check whether a land use is permitted in a zoning district |
| `get_dimensional_standards` | Get setbacks, density, height, and lot requirements for a district |
| `get_district_info` | Get district intent, character, and key rules |
| `get_special_requirements` | Look up Chapter 8 special requirements by section or keyword |
| `get_general_provisions` | Look up Chapter 2 general provisions — lot standards, infill setback rules, corner lots, ROW observation |
| `get_subdivision_requirements` | Subdivision types, procedures, plat and improvement requirements |
| `get_parcel_info` | Look up a parcel by PIN, address, or owner — returns zoning, jurisdiction, and property details from Rowan County GIS |
| `get_infill_context` | Find neighboring parcels within 300 ft for infill setback averaging under Section 2.2D |
| `can_i_build` | Complete answer to "Can I build X in district Y?" with permissions, special requirements, and dimensional standards |
| `search_ordinance` | Ranked search across the entire UDO — keywords or plain questions; stemming and synonyms ("ADU", "airbnb") |
| `get_udo_section` | Full text of any UDO section by number (`10.2.1`, `8.3`, `A.4`), or a chapter's table of contents |
| `define_term` | A defined term from UDO Chapter 3 (e.g. "home occupation", "flag lot") |
| `list_districts` | Quick reference of all 13 zoning district codes |
| `get_ordinance_section` | Get a section of the Town Code of Ordinances (non-zoning chapters) by number or keyword |
| `search_town_code` | Ranked search across the Town Code of Ordinances |
| `search_all` | Search the UDO and Code of Ordinances together as one ranked list, tagged by source |
| `get_160d_section` | Get the full text of a specific NCGS 160D section (state zoning law) |
| `search_160d` | Search NCGS Chapter 160D by keyword or phrase |
| `get_personnel_policy` | Get a provision of the Town Personnel Policies (HR manual) by id, section, or keyword |
| `search_personnel_policy` | Full-text search across the Town Personnel Policies |

## Setup

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Running

```bash
uv run main.py
```

Or use directly with an MCP client by pointing it at the server entry point.

## Data Sources

- **UDO text**: Markdown files converted from the official PDF chapters (`../markdown/`)
- **Structured data**: JSON files for permitted uses, dimensional standards, districts, special requirements, and subdivision procedures (`../data/`)
- **GIS**: Rowan County ArcGIS REST services for parcel geometry, zoning districts, ETJ boundaries, and corporate limits (public, no auth required)
- **NCGS 160D**: Full text of NC General Statute Chapter 160D — the state enabling statute for local zoning authority (`statutes/`). When the local UDO conflicts with state law, 160D controls.
- **Personnel Policies**: The Town's internal HR/employment manual (Sections I–X), converted from the official PDF into per-section markdown (`personnel/`) with an index (`data/personnel_index.json`). Regenerate with `python build_personnel.py` (requires the `pdftotext` binary). This is employee-relations law, not land use.

## Zoning Districts

| Code | Name |
|------|------|
| R-P | Rural Preservation |
| R-S | Suburban Residential |
| R-T | Town Residential |
| R-M | Mixed Residential |
| R-MH | Manufactured Home |
| N-C | Neighborhood Center |
| O-I | Office and Institutional |
| C-B | Central Business |
| H-B | Highway Business |
| C-P | Corporate Park |
| L-I | Light Industrial |
| H-I | Heavy Industrial |
| PUD | Planned Unit Development |

## License

For internal use by the Town of China Grove.

## Search

All search tools share one engine (`corpus.py`): every corpus is split into
sections keyed by their official number (UDO `10.2.1`, Code `26-81`,
`160D-108`, Personnel `IV-17.0`) and indexed with SQLite FTS5 — porter
stemming, whole-word matching, BM25 ranking over paragraph-sized chunks.
Plain-English questions work: stop words are dropped and common terms are
mapped to the ordinance's vocabulary through `data/search_synonyms.json`
(edit it freely). Results are whole sections, or the paragraphs and table rows
that match, each with its citation.

`evals/questions.json` holds real questions with the text that answers them;
`uv run python evals/run.py` measures retrieval, and `test_retrieval.py` runs
the same questions as tests.

## Regenerating the text

The markdown is converted from the official PDFs (`sources/udo/` for the UDO
chapters, the personnel and Town Code PDFs at the repo root). After any
conversion — a new ordinance chapter, or `build_personnel.py` — run:

```bash
uv run --group build python scripts/rebuild_tables.py   # ruled tables, re-read from the PDFs
uv run python scripts/clean_text.py                     # structure, borderless tables, reflow
uv run python scripts/reindex_lines.py                  # line numbers stored in data/*.json
uv run --with pytest pytest                             # incl. the table-vs-JSON cross-check
```

Each step is safe to re-run. `rebuild_tables.py` replaces a flattened table
only when the PDF table holds every word the text had; rows the PDF prints in
merged cells are corrected in its `CORRECTIONS` table, each checked against the
page image. `test_text.py` holds the Permitted Uses Table (from the PDF) and
`data/permitted_uses.json` (what the tools answer from) to agreement on all
212 uses.
