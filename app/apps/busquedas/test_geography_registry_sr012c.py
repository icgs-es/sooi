import copy
import json
from dataclasses import FrozenInstanceError
from unittest import TestCase

from apps.busquedas.geography_registry import (
    GeographyRegistry,
    GeographyType,
    RegistryValidationError,
    ResolutionStatus,
    load_default_registry,
    resolve,
)
from apps.busquedas.geography_registry.loader import canonical_content_digest


def provenance(label="fixture"):
    return [{
        "provenance_id": f"provenance:{label}",
        "kind": "test_contract",
        "reference": f"test://{label}",
        "note": f"Explicit test provenance for {label}.",
    }]


def identity(key, name, geo_type, *, province=None, parent=None):
    return {
        "canonical_key": key,
        "canonical_name": name,
        "type": geo_type,
        "province": province,
        "parent": parent,
        "provenance": provenance(key),
    }


def alias(alias_id, raw, target, *, province=None, geo_type=None):
    return {
        "alias_id": alias_id,
        "raw_alias": raw,
        "target_canonical_key": target,
        "province_scope": province,
        "type_scope": geo_type,
        "provenance": provenance(alias_id),
        "note": "Explicit fixture alias.",
    }


def membership(area, member):
    return {
        "area_canonical_key": area,
        "member_canonical_key": member,
        "provenance": provenance(f"membership:{area}:{member}"),
    }


def documents(identities, aliases=(), memberships=()):
    identities, aliases, memberships = list(identities), list(aliases), list(memberships)
    manifest = {
        "schema_version": 1,
        "registry_version": "TEST_REGISTRY_V1",
        "content_digest": canonical_content_digest(identities, aliases, memberships),
    }
    return manifest, identities, aliases, memberships


def registry(identities, aliases=(), memberships=()):
    return GeographyRegistry.from_documents(*documents(identities, aliases, memberships))


SYNTH_PROVINCE = identity("test:province:alpha", "Synthetic Province Alpha", "province")
SYNTH_MUNICIPALITY = identity(
    "test:municipality:alpha", "Synthetic Municipality Alpha", "municipality",
    province="test:province:alpha", parent="test:province:alpha",
)


class GeographyRegistryLoaderTests(TestCase):
    def test_default_manifest_and_digest_are_valid(self):
        loaded = load_default_registry()
        self.assertEqual(loaded.schema_version, 1)
        self.assertEqual(
            loaded.content_digest,
            "sha256:b83aa01504af08080fa4741e54f0184ecfe80fa428a4a96af6a0ceb24ee66519",
        )
        self.assertEqual(len(loaded.identities), 3)

    def test_digest_mismatch_fails_fast(self):
        manifest, identities, aliases, memberships = documents([SYNTH_PROVINCE])
        manifest["content_digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(RegistryValidationError, "digest mismatch"):
            GeographyRegistry.from_documents(manifest, identities, aliases, memberships)

    def test_duplicate_key_fails_fast(self):
        with self.assertRaisesRegex(RegistryValidationError, "duplicate canonical_key"):
            registry([SYNTH_PROVINCE, copy.deepcopy(SYNTH_PROVINCE)])

    def test_missing_parent_fails_fast(self):
        orphan = identity(
            "municipality:orphan", "Orphan", "municipality",
            province="province:missing", parent="province:missing",
        )
        with self.assertRaisesRegex(RegistryValidationError, "missing parent|missing province"):
            registry([orphan])

    def test_parent_cycle_fails_fast(self):
        province = identity("province:cycle", "Cycle Province", "province", parent="municipality:cycle")
        municipality = identity(
            "municipality:cycle", "Cycle Municipality", "municipality",
            province="province:cycle", parent="province:cycle",
        )
        with self.assertRaisesRegex(RegistryValidationError, "parent cycle detected"):
            registry([province, municipality])

    def test_invalid_parent_type_fails_fast(self):
        invalid = identity(
            "district:invalid", "Invalid District", "district",
            province="test:province:alpha", parent="test:province:alpha",
        )
        with self.assertRaisesRegex(RegistryValidationError, "invalid parent type"):
            registry([SYNTH_PROVINCE, invalid])

    def test_empty_identity_provenance_fails_fast(self):
        row = copy.deepcopy(SYNTH_PROVINCE)
        row["provenance"] = []
        with self.assertRaisesRegex(RegistryValidationError, "provenance"):
            registry([row])

    def test_empty_alias_provenance_fails_fast(self):
        row = alias("alias:x", "X", "test:municipality:alpha", province="test:province:alpha", geo_type="municipality")
        row["provenance"] = []
        with self.assertRaisesRegex(RegistryValidationError, "provenance"):
            registry([SYNTH_PROVINCE, SYNTH_MUNICIPALITY], [row])

    def test_conflicting_alias_fails_fast(self):
        other = identity(
            "municipality:other", "Other", "municipality",
            province="test:province:alpha", parent="test:province:alpha",
        )
        conflicting = alias(
            "alias:conflict", "Synthetic Municipality Alpha", "municipality:other",
            province="test:province:alpha", geo_type="municipality",
        )
        with self.assertRaisesRegex(RegistryValidationError, "lookup collision"):
            registry([SYNTH_PROVINCE, SYNTH_MUNICIPALITY, other], [conflicting])

    def test_normalized_lookup_collision_fails_fast(self):
        first = identity(
            "municipality:first", "Same  Name", "municipality",
            province="test:province:alpha", parent="test:province:alpha",
        )
        second = identity(
            "municipality:second", " same name ", "municipality",
            province="test:province:alpha", parent="test:province:alpha",
        )
        with self.assertRaisesRegex(RegistryValidationError, "lookup collision"):
            registry([SYNTH_PROVINCE, first, second])

    def test_loaded_data_is_immutable(self):
        loaded = load_default_registry()
        with self.assertRaises(TypeError):
            loaded.identities["new"] = object()
        with self.assertRaises(FrozenInstanceError):
            loaded.identities["es:malaga:province"].canonical_name = "Changed"


class GeographyResolverTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.default = load_default_registry()

    def test_canonical_exact(self):
        result = resolve(
            "  Puerto de la Torre  ", province_hint="MÁLAGA",
            expected_type=GeographyType.DISTRICT, registry=self.default,
        )
        self.assertEqual(result.status, ResolutionStatus.EXACT)
        self.assertEqual(result.canonical_name, "Puerto de la Torre")
        self.assertEqual(result.canonical_type, GeographyType.DISTRICT)

    def test_contract_alias_puerto_la_torre(self):
        result = resolve(
            "Puerto la Torre", province_hint="Málaga",
            expected_type="district", registry=self.default,
        )
        self.assertEqual(result.status, ResolutionStatus.ALIAS_RESOLVED)
        self.assertEqual(result.canonical_name, "Puerto de la Torre")
        self.assertEqual(result.canonical_type, GeographyType.DISTRICT)
        self.assertEqual(result.matched_alias, "Puerto la Torre")
        identity = self.default.identities[result.canonical_key]
        self.assertEqual(identity.parent, "es:malaga:municipality:malaga")
        self.assertEqual(identity.province, "es:malaga:province")
        self.assertTrue(result.provenance.alias_provenance)

    def test_unknown_is_unresolved(self):
        result = resolve("Never Declared", registry=self.default)
        self.assertEqual(result.status, ResolutionStatus.UNRESOLVED)
        self.assertIsNone(result.canonical_key)

    def test_homonym_without_hints_is_ambiguous(self):
        p1 = identity("province:one", "Province One", "province")
        p2 = identity("province:two", "Province Two", "province")
        m1 = identity("municipality:one", "Shared", "municipality", province="province:one", parent="province:one")
        m2 = identity("municipality:two", "Shared", "municipality", province="province:two", parent="province:two")
        loaded = registry([p1, p2, m1, m2])
        result = resolve("Shared", expected_type="municipality", registry=loaded)
        self.assertEqual(result.status, ResolutionStatus.AMBIGUOUS)
        self.assertEqual(result.candidates, ("municipality:one", "municipality:two"))

    def test_province_mismatch_is_outside_province(self):
        p2 = identity("province:other", "Other Province", "province")
        unique_municipality = identity(
            "municipality:unique", "Unique Town", "municipality",
            province="test:province:alpha", parent="test:province:alpha",
        )
        loaded = registry([SYNTH_PROVINCE, unique_municipality, p2])
        result = resolve(
            "Unique Town", province_hint="Other Province", expected_type="municipality",
            registry=loaded,
        )
        self.assertEqual(result.status, ResolutionStatus.OUTSIDE_PROVINCE)
        self.assertEqual(result.canonical_key, "municipality:unique")

    def test_type_mismatch_is_invalid_type(self):
        result = resolve(
            "Puerto de la Torre", province_hint="Málaga",
            expected_type="municipality", registry=self.default,
        )
        self.assertEqual(result.status, ResolutionStatus.INVALID_TYPE)
        self.assertEqual(result.canonical_type, GeographyType.DISTRICT)

    def test_unresolved_province_hint_does_not_choose_homonym(self):
        p1 = identity("province:one", "Province One", "province")
        p2 = identity("province:two", "Province Two", "province")
        m1 = identity("municipality:one", "Shared", "municipality", province="province:one", parent="province:one")
        m2 = identity("municipality:two", "Shared", "municipality", province="province:two", parent="province:two")
        loaded = registry([p1, p2, m1, m2])
        result = resolve("Shared", province_hint="Unknown Province", expected_type="municipality", registry=loaded)
        self.assertEqual(result.status, ResolutionStatus.AMBIGUOUS)


class SpainGenericCrossProvinceTests(TestCase):
    """Synthetic fixtures prove national generality without publishing real data."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.province_north = identity("test:province:north", "North Province", "province")
        cls.province_south = identity("test:province:south", "South Province", "province")
        cls.municipality_north = identity(
            "test:municipality:north:shared", "Shared Town", "municipality",
            province="test:province:north", parent="test:province:north",
        )
        cls.municipality_south = identity(
            "test:municipality:south:shared", "Shared Town", "municipality",
            province="test:province:south", parent="test:province:south",
        )
        cls.district = identity(
            "test:district:north:central", "Central District", "district",
            province="test:province:north", parent="test:municipality:north:shared",
        )
        cls.neighborhood = identity(
            "test:neighborhood:north:riverside", "Riverside Quarter", "neighborhood",
            province="test:province:north", parent="test:district:north:central",
        )
        cls.comarca = identity(
            "test:comarca:north:uplands", "Northern Uplands", "comarca",
            province="test:province:north", parent="test:province:north",
        )
        cls.named_area = identity(
            "test:named-area:north:portfolio", "Northern Portfolio", "named_area",
            province="test:province:north", parent="test:province:north",
        )
        aliases = [
            alias(
                "test:alias:north:common", "Common Alias", cls.municipality_north["canonical_key"],
                province=cls.province_north["canonical_key"], geo_type="municipality",
            ),
            alias(
                "test:alias:south:common", "Common Alias", cls.municipality_south["canonical_key"],
                province=cls.province_south["canonical_key"], geo_type="municipality",
            ),
        ]
        memberships = [
            membership(cls.comarca["canonical_key"], cls.municipality_north["canonical_key"]),
            membership(cls.named_area["canonical_key"], cls.district["canonical_key"]),
        ]
        cls.loaded = registry([
            cls.province_north, cls.province_south,
            cls.municipality_north, cls.municipality_south,
            cls.district, cls.neighborhood, cls.comarca, cls.named_area,
        ], aliases, memberships)

    def test_two_provinces_and_homonymous_municipalities_are_ambiguous_without_hint(self):
        result = resolve("Shared Town", expected_type="municipality", registry=self.loaded)
        self.assertEqual(result.status, ResolutionStatus.AMBIGUOUS)
        self.assertEqual(set(result.candidates), {
            "test:municipality:north:shared", "test:municipality:south:shared",
        })

    def test_same_alias_in_two_provinces_is_ambiguous_without_hint(self):
        result = resolve("Common Alias", expected_type="municipality", registry=self.loaded)
        self.assertEqual(result.status, ResolutionStatus.AMBIGUOUS)

    def test_same_alias_resolves_with_any_declared_province_hint(self):
        north = resolve(
            "Common Alias", province_hint="North Province",
            expected_type="municipality", registry=self.loaded,
        )
        south = resolve(
            "Common Alias", province_hint="South Province",
            expected_type="municipality", registry=self.loaded,
        )
        self.assertEqual(north.status, ResolutionStatus.ALIAS_RESOLVED)
        self.assertEqual(south.status, ResolutionStatus.ALIAS_RESOLVED)
        self.assertEqual(north.canonical_key, "test:municipality:north:shared")
        self.assertEqual(south.canonical_key, "test:municipality:south:shared")

    def test_every_v1_type_resolves_without_province_specific_logic(self):
        cases = (
            ("North Province", "province"),
            ("Shared Town", "municipality"),
            ("Central District", "district"),
            ("Riverside Quarter", "neighborhood"),
            ("Northern Uplands", "comarca"),
            ("Northern Portfolio", "named_area"),
        )
        for raw, expected_type in cases:
            with self.subTest(expected_type=expected_type):
                result = resolve(
                    raw, province_hint="North Province",
                    expected_type=expected_type, registry=self.loaded,
                )
                self.assertEqual(result.status, ResolutionStatus.EXACT)
                self.assertEqual(result.canonical_type.value, expected_type)

    def test_district_and_neighborhood_have_generic_hierarchy(self):
        district = self.loaded.identities["test:district:north:central"]
        neighborhood = self.loaded.identities["test:neighborhood:north:riverside"]
        self.assertEqual(district.parent, "test:municipality:north:shared")
        self.assertEqual(neighborhood.parent, "test:district:north:central")
        self.assertEqual(district.province, "test:province:north")

    def test_comarca_and_named_area_memberships_are_declarative(self):
        self.assertEqual(
            self.loaded.members_by_area["test:comarca:north:uplands"],
            ("test:municipality:north:shared",),
        )
        self.assertEqual(
            self.loaded.members_by_area["test:named-area:north:portfolio"],
            ("test:district:north:central",),
        )

    def test_municipality_parent_and_province_cannot_disagree(self):
        inconsistent = identity(
            "test:municipality:inconsistent", "Inconsistent Town", "municipality",
            province="test:province:south", parent="test:province:north",
        )
        with self.assertRaisesRegex(RegistryValidationError, "another province"):
            registry([self.province_north, self.province_south, inconsistent])

    def test_invalid_type_is_generic(self):
        result = resolve(
            "Central District", province_hint="North Province",
            expected_type="municipality", registry=self.loaded,
        )
        self.assertEqual(result.status, ResolutionStatus.INVALID_TYPE)

    def test_outside_province_is_generic(self):
        result = resolve(
            "Central District", province_hint="South Province",
            expected_type="district", registry=self.loaded,
        )
        self.assertEqual(result.status, ResolutionStatus.OUTSIDE_PROVINCE)

    def test_equal_canonical_names_at_different_levels_need_type(self):
        municipality = identity(
            "test:municipality:north:north-province", "North Province", "municipality",
            province="test:province:north", parent="test:province:north",
        )
        loaded = registry([self.province_north, municipality])
        ambiguous = resolve("North Province", registry=loaded)
        as_province = resolve("North Province", expected_type="province", registry=loaded)
        as_municipality = resolve("North Province", expected_type="municipality", registry=loaded)
        self.assertEqual(ambiguous.status, ResolutionStatus.AMBIGUOUS)
        self.assertEqual(as_province.canonical_key, "test:province:north")
        self.assertEqual(as_municipality.canonical_key, municipality["canonical_key"])

    def test_unregistered_typo_remains_unresolved(self):
        result = resolve(
            "Shared Tawn", province_hint="North Province",
            expected_type="municipality", registry=self.loaded,
        )
        self.assertEqual(result.status, ResolutionStatus.UNRESOLVED)


class GeographySerializationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.default = load_default_registry()

    def test_empty_is_unresolved(self):
        for raw in (None, "", "   "):
            with self.subTest(raw=raw):
                self.assertEqual(resolve(raw, registry=self.default).status, ResolutionStatus.UNRESOLVED)

    def test_serialization_is_stable_and_deterministic(self):
        first = resolve("Puerto la Torre", "Málaga", "district", registry=self.default)
        second = resolve("Puerto la Torre", "Málaga", "district", registry=self.default)
        self.assertEqual(first, second)
        self.assertEqual(first.to_json(), second.to_json())
        self.assertEqual(json.loads(first.to_json()), first.to_dict())


class GeographyNoInventionContractTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.registry = load_default_registry()

    def assert_unresolved(self, raw, **kwargs):
        result = resolve(raw, registry=self.registry, **kwargs)
        self.assertEqual(result.status, ResolutionStatus.UNRESOLVED)
        self.assertIsNone(result.canonical_key)

    def test_url_does_not_resolve(self):
        self.assert_unresolved("https://example.test/puerto-de-la-torre/listing/1")

    def test_query_or_batch_text_does_not_resolve(self):
        self.assert_unresolved("search_location=Puerto de la Torre")
        self.assert_unresolved("batch: Málaga | Puerto de la Torre")

    def test_similarity_and_typo_do_not_resolve(self):
        self.assert_unresolved("Puerto de Torre")
        self.assert_unresolved("Puerta de la Torre")

    def test_province_hint_alone_does_not_create_municipality(self):
        self.assert_unresolved("", province_hint="Málaga", expected_type="municipality")

    def test_requested_geography_is_not_candidate_evidence(self):
        requested = resolve("Puerto la Torre", "Málaga", "district", registry=self.registry)
        candidate = resolve(None, "Málaga", "district", registry=self.registry)
        self.assertEqual(requested.status, ResolutionStatus.ALIAS_RESOLVED)
        self.assertEqual(candidate.status, ResolutionStatus.UNRESOLVED)
        self.assertIsNone(candidate.canonical_key)
