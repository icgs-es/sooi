"""Pure, deterministic builder for the official INE Spain geography registry."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET

from .loader import canonical_content_digest

_XLSX_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_EXPECTED_INE_HEADERS = ("CODAUTO", "CPRO", "CMUN", "DC", "NOMBRE")


def _xml_tag(name: str) -> str:
    return f"{{{_XLSX_MAIN}}}{name}"


def _column_number(reference: str) -> int:
    letters = re.match(r"([A-Za-z]+)", reference or "")
    if not letters:
        raise ValueError(f"invalid XLSX cell reference: {reference!r}")
    result = 0
    for char in letters.group(1).upper():
        result = result * 26 + ord(char) - ord("A") + 1
    return result


def _inline_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext())


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [_inline_text(item) for item in root.findall(_xml_tag("si"))]


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return _inline_text(cell.find(_xml_tag("is")))
    value = cell.find(_xml_tag("v"))
    raw = _inline_text(value).strip()
    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError) as exc:
            raise ValueError("invalid shared string cell") from exc
    if cell_type == "str":
        return raw
    if cell_type == "b":
        return "TRUE" if raw == "1" else "FALSE" if raw == "0" else raw
    return raw


def _code_string(value: object, width: int) -> str:
    """Preserve an official code when XLSX serializes it as text or a number."""
    raw = str(value).strip()
    decimal_integer = re.fullmatch(r"(\d+)\.0+", raw)
    if decimal_integer:
        raw = decimal_integer.group(1)
    if not raw.isdigit():
        raise ValueError(f"invalid official code: {value!r}")
    return raw.zfill(width)


def _worksheet_path(archive: zipfile.ZipFile, relationship_id: str) -> str:
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for relation in rels.findall(f"{{{_PKG_REL}}}Relationship"):
        if relation.attrib.get("Id") == relationship_id:
            target = relation.attrib.get("Target", "")
            if target.startswith("/"):
                return target.lstrip("/")
            return posixpath.normpath(posixpath.join("xl", target))
    raise ValueError(f"worksheet relationship not found: {relationship_id}")


def _worksheet_rows(archive: zipfile.ZipFile, path: str, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(archive.read(path))
    rows = []
    for row_element in root.findall(f".//{_xml_tag('row')}"):
        cells = {}
        next_column = 1
        for cell in row_element.findall(_xml_tag("c")):
            reference = cell.attrib.get("r")
            column = _column_number(reference) if reference else next_column
            cells[column] = _cell_value(cell, shared)
            next_column = column + 1
        if cells:
            width = max(cells)
            rows.append([cells.get(index, "") for index in range(1, width + 1)])
        else:
            rows.append([])
    return rows


def read_ine_xlsx(path: Path, *, requested_sheet: str | None = None,
                  expected_headers: tuple[str, ...] = _EXPECTED_INE_HEADERS) -> tuple[str, list[list[str]]]:
    """Read an INE workbook using only the Python standard library.

    The result begins immediately after the detected header row. Cell references,
    rather than XML position, determine columns so sparse worksheets are safe.
    """
    path = Path(path)
    if not zipfile.is_zipfile(path):
        raise ValueError("source is not a valid XLSX zip archive")
    try:
        with zipfile.ZipFile(path) as archive:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            shared = _shared_strings(archive)
            sheets = workbook.find(_xml_tag("sheets"))
            if sheets is None:
                raise ValueError("XLSX workbook has no worksheets")
            candidates = []
            for sheet in sheets.findall(_xml_tag("sheet")):
                name = sheet.attrib.get("name", "")
                if requested_sheet is not None and name != requested_sheet:
                    continue
                rel_id = sheet.attrib.get(f"{{{_XLSX_REL}}}id")
                if not rel_id:
                    raise ValueError(f"worksheet has no relationship: {name}")
                candidates.append((name, _worksheet_path(archive, rel_id)))
            if requested_sheet is not None and not candidates:
                raise ValueError(f"worksheet not found: {requested_sheet}")
            for name, worksheet in candidates:
                rows = _worksheet_rows(archive, worksheet, shared)
                for position, row in enumerate(rows):
                    if tuple(str(value).strip() for value in row[:len(expected_headers)]) == expected_headers:
                        return name, rows[position + 1:]
            raise ValueError("expected XLSX header not found")
    except (KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise ValueError("invalid XLSX workbook") from exc


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], [], []
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag in {"td", "th"}:
            self.in_cell = True
            self.cell = []
        elif tag == "tr":
            self.row = []

    def handle_data(self, data):
        if self.in_cell:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.in_cell:
            self.row.append(" ".join("".join(self.cell).split()))
            self.in_cell = False
        elif tag == "tr" and self.row:
            self.rows.append(self.row)


def _html_rows(path: Path) -> list[list[str]]:
    parser = _TableParser()
    parser.feed(path.read_text(encoding="iso-8859-1", errors="replace"))
    return parser.rows


def _ccaa(path: Path) -> dict[str, str]:
    rows = _html_rows(path)
    return {row[0].zfill(2): row[1] for row in rows if len(row) >= 2 and re.fullmatch(r"\d{2}", row[0])}


def _ccaa_provinces(path: Path) -> dict[str, tuple[str, str]]:
    rows = _html_rows(path)
    return {
        row[2].zfill(2): (row[0].zfill(2), row[3])
        for row in rows
        if len(row) >= 4 and re.fullmatch(r"\d{2}", row[0]) and re.fullmatch(r"\d{2}", row[2])
    }


def _modification_count(path: Path) -> int:
    rows = _html_rows(path)
    # The current page has one row per denomination change; blank rows are excluded.
    return sum(1 for row in rows if len(row) >= 5 and re.fullmatch(r"\d{2} \d{3} \d", row[2]))


def _verify_source_hashes(source: Path) -> None:
    sums = source / "SHA256SUMS.txt"
    if not sums.exists():
        raise ValueError("SHA256SUMS.txt is required")
    for line in sums.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) < 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            continue
        target = source / parts[-1].lstrip("*")
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != parts[0]:
            raise ValueError(f"source hash mismatch: {target.name}")


def _official_province_counts(path: Path, province_map: dict[str, tuple[str, str]]) -> dict[str, int]:
    counts = {}
    names = {name: code for code, (_community, name) in province_map.items()}
    for row in _html_rows(path):
        if len(row) < 2 or row[0] in {"Provincias", "TOTAL", "Islas"}:
            continue
        value = re.sub(r"\.", "", row[1])
        if row[0] in names and value.isdigit():
            counts[names[row[0]]] = int(value)
    return counts


def _identity(key, name, kind, *, official_code=None, parent=None, community=None, province=None, query_label=None, control_digit=None, source="INE", source_version="2026-01-01"):
    return {
        "canonical_key": key,
        "canonical_name": name,
        "type": kind,
        "official_code": official_code,
        "country_code": "ES",
        "parent": parent,
        "province": province,
        "autonomous_community_key": community,
        "province_key": province,
        "query_label": query_label or name,
        "control_digit": control_digit,
        "source": source,
        "source_version": source_version,
        "provenance": [{
            "provenance_id": f"ine-2026-{key.replace(':', '-')}",
            "kind": "official_ine_dataset",
            "reference": "diccionario26.xlsx",
            "note": "INE relationship at 01/01/2026",
        }],
    }


def build_documents(source: Path) -> tuple[list[dict], list[dict], list[dict], dict]:
    source = Path(source)
    _verify_source_hashes(source)
    _sheet_name, rows = read_ine_xlsx(source / "diccionario26.xlsx")
    if len(rows) != 8132:
        raise ValueError(f"expected 8132 municipality rows, got {len(rows)}")
    ccaa = _ccaa(source / "cod_ccaa.htm")
    ccaa_provinces = _ccaa_provinces(source / "cod_ccaa_provincia.htm")
    if len(ccaa) != 19 or len(ccaa_provinces) != 52:
        raise ValueError("official autonomous/province inventory is incomplete")
    official_counts = _official_province_counts(source / "cod_num_muni_provincia_ccaa.htm", ccaa_provinces)

    identities = [_identity("country:ES", "España", "country", official_code="ES", parent=None)]
    for code, name in sorted(ccaa.items()):
        kind = "autonomous_city" if code in {"18", "19"} else "autonomous_community"
        identities.append(_identity(f"{kind}:{code}", name, kind, official_code=code, parent="country:ES"))
    for code, (community, name) in sorted(ccaa_provinces.items()):
        if code in {"51", "52"}:
            continue
        identities.append(_identity(
            f"province:{code}", name, "province", official_code=code,
            parent=f"autonomous_community:{community}", community=f"autonomous_community:{community}",
        ))

    seen = set()
    for auto, cpro, cmun, dc, name in rows:
        auto, cpro, cmun, dc, name = _code_string(auto, 2), _code_string(cpro, 2), _code_string(cmun, 3), _code_string(dc, 1), str(name).strip()
        code = cpro + cmun
        if code in seen or not name or cpro not in ccaa_provinces:
            raise ValueError(f"invalid or duplicate municipality row {code}")
        seen.add(code)
        community, province_name = ccaa_provinces[cpro]
        city = cpro in {"51", "52"}
        parent = f"autonomous_city:{community}" if city else f"province:{cpro}"
        province_key = None if city else parent
        identities.append(_identity(
            f"municipality:{code}", name, "municipality", official_code=code,
            parent=parent, community=(f"autonomous_city:{community}" if city else f"autonomous_community:{community}"),
            province=province_key, query_label=f"{name}, {province_name}", control_digit=dc,
        ))
    if len(seen) != 8132:
        raise ValueError(f"expected 8132 unique municipality codes, got {len(seen)}")
    actual_counts = {}
    for row in rows:
        province_code = _code_string(row[1], 2)
        actual_counts[province_code] = actual_counts.get(province_code, 0) + 1
    if official_counts != actual_counts:
        raise ValueError("province municipality counts do not reconcile with official INE table")

    aliases = []
    # INE names use the official inverted article form (e.g. ``Palmas, Las``).
    # Add only this deterministic orthographic variant; no colloquial aliases.
    for identity in identities:
        if identity["type"] != "municipality" or ", " not in identity["canonical_name"]:
            continue
        stem, article = identity["canonical_name"].rsplit(", ", 1)
        if article in {"A", "El", "La", "Las", "Los", "O"}:
            aliases.append({
                "alias_id": f"{identity['canonical_key']}:alias:article",
                "raw_alias": f"{article} {stem}",
                "target_canonical_key": identity["canonical_key"],
                "province_scope": identity.get("province"),
                "type_scope": "municipality",
                "provenance": [{
                    "provenance_id": f"ine-2026-{identity['canonical_key'].replace(':', '-')}-article",
                    "kind": "deterministic_orthographic_alias",
                    "reference": "diccionario26.xlsx",
                    "note": "Article moved to natural Spanish query order",
                }],
                "note": "Deterministic official-name article variant",
            })
    memberships = []
    metadata = {
        "source_reference_date": "2026-01-01",
        "source_authority": "INE",
        "source_sha256": hashlib.sha256((source / "diccionario26.xlsx").read_bytes()).hexdigest(),
        "post_baseline_modifications_discovered": _modification_count(source / "codmun_anual.htm"),
        "post_baseline_modifications_applied": False,
        "source_files": sorted(p.name for p in source.iterdir() if p.is_file()),
    }
    return identities, aliases, memberships, metadata


def write_registry(source: Path, output: Path, version: str, *, apply: bool = False) -> dict:
    identities, aliases, memberships, metadata = build_documents(Path(source))
    manifest = {
        "registry_version": version,
        "schema_version": 2,
        "country": "ES",
        **metadata,
        "entity_counts": {"country": 1, "autonomous_community": 17, "autonomous_city": 2, "province": 50, "municipality": 8132, "island": 0, "territorial_area": 0},
        "alias_count": len(aliases),
        "membership_count": 0,
        "validation": "PASS",
    }
    manifest["content_digest"] = canonical_content_digest(identities, aliases, memberships)
    if apply:
        output.mkdir(parents=True, exist_ok=True)
        for name, value in (("manifest.json", manifest), ("identities.json", identities), ("aliases.json", aliases), ("memberships.json", memberships)):
            (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
