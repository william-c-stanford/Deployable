"""Tests for the skills taxonomy and certifications seed data.

These are pure-data validation tests — no database connection required.
They verify the seed records are well-formed, unique, and meet the
acceptance criteria (15-20 skills, industry-standard certs).
"""

import uuid
import pytest

from app.seeds.skills_and_certs import (
    SKILL_CATEGORIES,
    SKILLS,
    CERTIFICATIONS,
    _uuid,
)


# ------------------------------------------------------------------
# Skills taxonomy
# ------------------------------------------------------------------

class TestSkillCategories:
    def test_at_least_3_categories(self):
        assert len(SKILL_CATEGORIES) >= 3

    def test_unique_names(self):
        names = [c["name"] for c in SKILL_CATEGORIES]
        assert len(names) == len(set(names))

    def test_unique_ids(self):
        ids = [c["id"] for c in SKILL_CATEGORIES]
        assert len(ids) == len(set(ids))

    def test_all_have_required_fields(self):
        for cat in SKILL_CATEGORIES:
            assert "id" in cat
            assert "name" in cat
            assert "display_order" in cat


class TestSkills:
    def test_between_15_and_20_skills(self):
        assert 15 <= len(SKILLS) <= 20, f"Expected 15-20 skills, got {len(SKILLS)}"

    def test_unique_names(self):
        names = [s["name"] for s in SKILLS]
        assert len(names) == len(set(names))

    def test_unique_slugs(self):
        slugs = [s["slug"] for s in SKILLS]
        assert len(slugs) == len(set(slugs))

    def test_unique_ids(self):
        ids = [s["id"] for s in SKILLS]
        assert len(ids) == len(set(ids))

    def test_all_reference_valid_category(self):
        valid_cat_ids = {c["id"] for c in SKILL_CATEGORIES}
        for skill in SKILLS:
            assert skill["category_id"] in valid_cat_ids, (
                f"Skill '{skill['name']}' references invalid category_id"
            )

    def test_hours_thresholds_are_positive(self):
        for skill in SKILLS:
            assert skill["intermediate_hours_threshold"] > 0
            assert skill["advanced_hours_threshold"] > skill["intermediate_hours_threshold"]

    def test_required_fields_present(self):
        required = {"id", "name", "slug", "category_id",
                     "intermediate_hours_threshold", "advanced_hours_threshold"}
        for skill in SKILLS:
            missing = required - set(skill.keys())
            assert not missing, f"Skill '{skill['name']}' missing fields: {missing}"

    def test_expected_core_skills_present(self):
        """Verify key skills from the AC are included."""
        names = {s["name"] for s in SKILLS}
        expected = {
            "Fiber Splicing",
            "OTDR Testing",
            "Fiber Cable Pulling",
            "Rack & Cabinet Mounting",
        }
        for exp in expected:
            assert exp in names, f"Expected skill '{exp}' not found"

    def test_cert_gates_reference_valid_certs(self):
        """If a skill has a cert gate, the cert name should exist in CERTIFICATIONS."""
        cert_names = {c["name"] for c in CERTIFICATIONS}
        for skill in SKILLS:
            for gate_field in ("cert_gate_intermediate", "cert_gate_advanced"):
                gate = skill.get(gate_field)
                if gate:
                    assert gate in cert_names, (
                        f"Skill '{skill['name']}' cert gate '{gate}' not in certifications"
                    )


# ------------------------------------------------------------------
# Certifications
# ------------------------------------------------------------------

class TestCertifications:
    def test_at_least_10_certs(self):
        assert len(CERTIFICATIONS) >= 10, f"Expected >=10 certs, got {len(CERTIFICATIONS)}"

    def test_unique_names(self):
        names = [c["name"] for c in CERTIFICATIONS]
        assert len(names) == len(set(names))

    def test_unique_slugs(self):
        slugs = [c["slug"] for c in CERTIFICATIONS]
        assert len(slugs) == len(set(slugs))

    def test_unique_ids(self):
        ids = [c["id"] for c in CERTIFICATIONS]
        assert len(ids) == len(set(ids))

    def test_validity_months_non_negative(self):
        for cert in CERTIFICATIONS:
            assert cert["validity_months"] >= 0

    def test_cert_categories_valid(self):
        allowed = {"industry", "safety", "vendor", "government"}
        for cert in CERTIFICATIONS:
            assert cert["cert_category"] in allowed, (
                f"Cert '{cert['name']}' has invalid category '{cert['cert_category']}'"
            )

    def test_expected_certs_present(self):
        """Verify the key certs from the AC are included."""
        names = {c["name"] for c in CERTIFICATIONS}
        expected = {"FOA CFOT", "BICSI Technician", "OSHA 30", "OSHA 10"}
        for exp in expected:
            assert exp in names, f"Expected cert '{exp}' not found"

    def test_required_fields_present(self):
        required = {"id", "name", "slug", "issuing_body", "validity_months", "cert_category"}
        for cert in CERTIFICATIONS:
            missing = required - set(cert.keys())
            assert not missing, f"Cert '{cert['name']}' missing fields: {missing}"

    def test_has_multiple_categories(self):
        """Ensure diversity — at least 3 different cert categories."""
        categories = {c["cert_category"] for c in CERTIFICATIONS}
        assert len(categories) >= 3


# ------------------------------------------------------------------
# Deterministic UUID helper
# ------------------------------------------------------------------

class TestUuidHelper:
    def test_deterministic(self):
        assert _uuid("test", "foo") == _uuid("test", "foo")

    def test_different_inputs_different_outputs(self):
        assert _uuid("test", "foo") != _uuid("test", "bar")

    def test_valid_uuid_format(self):
        result = _uuid("test", "sample")
        parsed = uuid.UUID(result)
        assert parsed.version == 5
