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
| `search_ordinance` | Full-text search across the entire UDO (with paragraph context) |
| `list_districts` | Quick reference of all 13 zoning district codes |
| `get_160d_section` | Get the full text of a specific NCGS 160D section (state zoning law) |
| `search_160d` | Search NCGS Chapter 160D by keyword or phrase |

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
