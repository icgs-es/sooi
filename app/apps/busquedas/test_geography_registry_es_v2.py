import hashlib
import inspect
import json
import os
import zipfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import escape

from .geography_registry import importer
from .geography_registry.importer import build_documents, read_ine_xlsx, write_registry
from .geography_registry.loader import load_default_registry, load_v2_registry
from .geography_registry.resolver import resolve


APP_ROOT = Path(__file__).resolve().parents[2]
V1_PATH = APP_ROOT / "apps" / "busquedas" / "geography_registry" / "data" / "v1"
V2_PATH = APP_ROOT / "apps" / "busquedas" / "geography_registry" / "data" / "v2"
OFFICIAL_SOURCE_ENV = "SOOI_GEOGRAPHY_OFFICIAL_SOURCE"
SOURCE = Path(os.environ[OFFICIAL_SOURCE_ENV]) if os.environ.get(OFFICIAL_SOURCE_ENV) else None


def _require_official_source() -> Path:
    if SOURCE is None:
        raise unittest.SkipTest(
            f"{OFFICIAL_SOURCE_ENV} is required for tests that consume the raw INE source"
        )
    if not SOURCE.is_dir():
        raise AssertionError(f"official geography source directory does not exist: {SOURCE}")
    return SOURCE


def _make_xlsx(path: Path, *, rows, shared=False, sheet_name="Data"):
    strings = []
    if shared:
        for row in rows:
            for value, cell_type in row.values():
                if cell_type == "s" and value not in strings:
                    strings.append(value)
    def cell(reference, value, cell_type):
        if cell_type == "s":
            body = f"<v>{strings.index(value)}</v>"
            return f'<c r="{reference}" t="s">{body}</c>'
        if cell_type == "inlineStr":
            return f'<c r="{reference}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
        return f'<c r="{reference}" t="{cell_type}"><v>{escape(value)}</v></c>'
    row_xml = []
    for row_number, cells in enumerate(rows, 1):
        row_xml.append(f'<row r="{row_number}">' + "".join(cell(ref, value, kind) for ref, (value, kind) in cells.items()) + "</row>")
    worksheet = "<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>" + "".join(row_xml) + "</sheetData></worksheet>"
    workbook = f'''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        if shared:
            shared_xml = '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="%d" uniqueCount="%d">%s</sst>' % (len(strings), len(strings), "".join(f"<si><t>{escape(value)}</t></si>" for value in strings))
            archive.writestr("xl/sharedStrings.xml", shared_xml)


class SpainGeographyRegistryV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_v2_registry()

    def test_source_sha_rows_and_reference_date(self):
        source = _require_official_source()
        self.assertEqual(
            hashlib.sha256((source / "diccionario26.xlsx").read_bytes()).hexdigest(),
            "07f8e8d64eba73fe9d425196fe82f4e09a9886a287abd650e88ca8d98dd49052",
        )
        identities, _aliases, _memberships, metadata = build_documents(source)
        self.assertEqual(metadata["source_reference_date"], "2026-01-01")
        self.assertEqual(sum(row["type"] == "municipality" for row in identities), 8132)

    def test_national_inventory_and_keys(self):
        self.assertEqual(len(self.registry.identities), 8202)
        self.assertEqual(sum(i.type.value == "municipality" for i in self.registry.identities.values()), 8132)
        self.assertEqual(sum(i.type.value == "autonomous_community" for i in self.registry.identities.values()), 17)
        self.assertEqual(sum(i.type.value == "autonomous_city" for i in self.registry.identities.values()), 2)
        self.assertEqual(sum(i.type.value == "province" for i in self.registry.identities.values()), 50)
        codes = [i.official_code for i in self.registry.identities.values() if i.type.value == "municipality"]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue(all(isinstance(code, str) and len(code) == 5 for code in codes))

    def test_los_pedroches_resolves_without_fallback(self):
        locations = [
            "Pozoblanco", "Alcaracejos", "Añora", "Belalcázar", "Cardeña", "Conquista",
            "Dos Torres", "El Guijo", "El Viso", "Fuente La Lancha", "Hinojosa del Duque",
            "Pedroche", "Santa Eufemia", "Torrecampo", "Villanueva de Córdoba",
            "Villanueva del Duque", "Villaralto",
        ]
        for location in locations:
            result = resolve(location, province_hint="Córdoba", expected_type="municipality", registry=self.registry)
            self.assertIn(result.status.value, {"EXACT", "ALIAS_RESOLVED"}, location)
            self.assertEqual(self.registry.identities[result.canonical_key].province, "province:14")

    def test_national_fixtures_and_safe_ambiguity(self):
        for location in ("Madrid", "Barcelona", "Málaga", "A Coruña", "Palma", "Las Palmas de Gran Canaria", "Santa Cruz de Tenerife", "Ceuta", "Melilla"):
            result = resolve(location, expected_type="municipality", registry=self.registry)
            self.assertIn(result.status.value, {"EXACT", "ALIAS_RESOLVED"}, location)
        self.assertEqual(resolve("Córdoba", registry=self.registry).status.value, "AMBIGUOUS")
        self.assertEqual(resolve("place that does not exist", registry=self.registry).status.value, "UNRESOLVED")

    def test_query_labels_and_v1_unchanged(self):
        result = resolve("Pozoblanco", province_hint="Córdoba", expected_type="municipality", registry=self.registry)
        self.assertEqual(result.canonical_name, "Pozoblanco")
        self.assertEqual(self.registry.identities[result.canonical_key].query_label, "Pozoblanco, Córdoba")
        self.assertEqual(load_default_registry().registry_version, "SOOI_GEOGRAPHY_REGISTRY_V1_2026_08_14")
        self.assertTrue((V1_PATH / "identities.json").exists())

    def test_build_is_deterministic_and_dry_run_does_not_write(self):
        source = _require_official_source()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "v2"
            first = write_registry(source, output, "SOOI_GEOGRAPHY_REGISTRY_ES_V2", apply=True)
            before = {name: (output / name).read_bytes() for name in ("identities.json", "aliases.json", "memberships.json")}
            second = write_registry(source, output, "SOOI_GEOGRAPHY_REGISTRY_ES_V2", apply=False)
            self.assertEqual(first["content_digest"], second["content_digest"])
            self.assertEqual(before, {name: (output / name).read_bytes() for name in before})

    def test_schema_capabilities_present_and_no_db_path(self):
        manifest = json.loads((V2_PATH / "manifest.json").read_text())
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["entity_counts"]["island"], 0)
        self.assertEqual(manifest["entity_counts"]["territorial_area"], 0)

    def test_portable_project_and_registry_paths_exist(self):
        self.assertTrue(APP_ROOT.is_dir())
        self.assertTrue((APP_ROOT / "apps").is_dir())
        for path in (V1_PATH, V2_PATH):
            self.assertTrue(path.is_dir(), path)
        for name in ("manifest.json", "identities.json", "aliases.json", "memberships.json"):
            self.assertTrue((V2_PATH / name).is_file(), name)
        self.assertTrue((V1_PATH / "identities.json").is_file())

    def test_official_source_is_explicitly_configured(self):
        if SOURCE is None:
            self.skipTest(f"set {OFFICIAL_SOURCE_ENV} for raw-source importer tests")
        self.assertEqual(SOURCE, Path(os.environ[OFFICIAL_SOURCE_ENV]))
        self.assertTrue((SOURCE / "diccionario26.xlsx").is_file())
        self.assertTrue((SOURCE / "SHA256SUMS.txt").is_file())

    def test_importer_is_standard_library_only(self):
        self.assertNotIn("openpyxl", importer.__dict__)
        self.assertNotIn("openpyxl", inspect.getsource(importer))

    def test_xlsx_reader_supports_shared_inline_numeric_and_sparse_cells(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.xlsx"
            _make_xlsx(path, shared=True, rows=[
                {"A1": ("CODAUTO", "s"), "B1": ("CPRO", "s"), "C1": ("CMUN", "s"), "D1": ("DC", "s"), "E1": ("NOMBRE", "s")},
                {"A2": ("1", "n"), "C2": ("7", "n"), "D2": ("0", "n"), "E2": ("Nombre", "inlineStr")},
                {"A3": ("1", "n"), "B3": ("02", "n"), "C3": ("003", "n"), "D3": ("4", "n"), "E3": ("Otro", "inlineStr")},
            ])
            sheet, rows = read_ine_xlsx(path)
            self.assertEqual(sheet, "Data")
            self.assertEqual(rows[0], ["1", "", "7", "0", "Nombre"])
            self.assertEqual(rows[1], ["1", "02", "003", "4", "Otro"])

    def test_xlsx_reader_selects_header_sheet_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "inline.xlsx"
            _make_xlsx(path, rows=[
                {"A1": ("CODAUTO", "inlineStr"), "B1": ("CPRO", "inlineStr"), "C1": ("CMUN", "inlineStr"), "D1": ("DC", "inlineStr"), "E1": ("NOMBRE", "inlineStr")},
                {"A2": ("01", "inlineStr"), "B2": ("14", "inlineStr"), "C2": ("054", "inlineStr"), "D2": ("0", "inlineStr"), "E2": ("Pozoblanco", "inlineStr")},
            ], sheet_name="not_dic25")
            _sheet, rows = read_ine_xlsx(path)
            self.assertEqual(rows[0][1:3], ["14", "054"])
            invalid = Path(temp) / "invalid.xlsx"
            invalid.write_bytes(b"not an xlsx")
            with self.assertRaises(ValueError):
                read_ine_xlsx(invalid)
            missing = Path(temp) / "missing.xlsx"
            _make_xlsx(missing, rows=[{"A1": ("WRONG", "inlineStr")}])
            with self.assertRaises(ValueError):
                read_ine_xlsx(missing)
