"""Tests for training models, schemas, and advancement with cert gates.

Tests cover:
  - TrainingProgram creation and fields
  - TrainingEnrollment creation with Apprentice/Intermediate/Advanced levels
  - TrainingHoursLog creation and positive hours constraint
  - AdvancementGateConfig creation and uniqueness
  - AdvancementLevel enum values match spec (Apprentice/Intermediate/Advanced)
  - ProficiencyLevel enum values match spec (Apprentice/Intermediate/Advanced)
  - Seed data loading for training programs and gate configs
"""

import uuid
from datetime import date, datetime

import pytest

from app.models.training import (
    TrainingProgram,
    TrainingEnrollment,
    TrainingHoursLog,
    AdvancementGateConfig,
    AdvancementLevel,
    EnrollmentStatus,
    HoursLogSource,
)
from app.models.technician import ProficiencyLevel
from app.schemas.training import (
    TrainingProgramCreate,
    TrainingEnrollmentCreate,
    TrainingHoursLogCreate,
    AdvancementGateConfigCreate,
    TechnicianTrainingSummary,
)
from app.seeds.training_programs import (
    TRAINING_PROGRAMS,
    ADVANCEMENT_GATE_CONFIGS,
    seed_training_programs,
)


# ---------------------------------------------------------------------------
# Enum Value Tests
# ---------------------------------------------------------------------------

class TestAdvancementLevelEnum:
    """Verify the AdvancementLevel enum has the correct values."""

    def test_has_apprentice(self):
        assert AdvancementLevel.APPRENTICE.value == "Apprentice"

    def test_has_intermediate(self):
        assert AdvancementLevel.INTERMEDIATE.value == "Intermediate"

    def test_has_advanced(self):
        assert AdvancementLevel.ADVANCED.value == "Advanced"

    def test_exactly_three_levels(self):
        assert len(AdvancementLevel) == 3

    def test_string_coercion(self):
        """AdvancementLevel is a str enum, so it should be directly comparable."""
        assert AdvancementLevel.APPRENTICE == "Apprentice"


class TestProficiencyLevelEnum:
    """Verify ProficiencyLevel was updated to Apprentice."""

    def test_has_apprentice(self):
        assert ProficiencyLevel.APPRENTICE.value == "Apprentice"

    def test_no_beginner(self):
        """BEGINNER should no longer exist."""
        assert not hasattr(ProficiencyLevel, "BEGINNER")

    def test_has_intermediate(self):
        assert ProficiencyLevel.INTERMEDIATE.value == "Intermediate"

    def test_has_advanced(self):
        assert ProficiencyLevel.ADVANCED.value == "Advanced"


class TestEnrollmentStatusEnum:
    def test_values(self):
        assert EnrollmentStatus.ACTIVE.value == "Active"
        assert EnrollmentStatus.COMPLETED.value == "Completed"
        assert EnrollmentStatus.PAUSED.value == "Paused"
        assert EnrollmentStatus.WITHDRAWN.value == "Withdrawn"


class TestHoursLogSourceEnum:
    def test_values(self):
        expected = {"Timesheet", "Classroom", "Online", "Field Training", "Assessment", "Manual"}
        actual = {s.value for s in HoursLogSource}
        assert actual == expected


# ---------------------------------------------------------------------------
# Model Instantiation Tests (no DB required)
# ---------------------------------------------------------------------------

class TestTrainingProgramModel:
    def test_create_instance(self):
        prog = TrainingProgram(
            name="Test Program",
            slug="test-program",
            description="A test training program",
            total_hours_required=500.0,
            intermediate_hours_threshold=100.0,
            advanced_hours_threshold=300.0,
        )
        assert prog.name == "Test Program"
        assert prog.slug == "test-program"
        assert prog.intermediate_hours_threshold == 100.0
        assert prog.advanced_hours_threshold == 300.0

    def test_repr(self):
        prog = TrainingProgram(name="Fiber Basics")
        assert "Fiber Basics" in repr(prog)

    def test_tablename(self):
        assert TrainingProgram.__tablename__ == "training_programs"


class TestTrainingEnrollmentModel:
    def test_create_instance(self):
        enrollment = TrainingEnrollment(
            technician_id=uuid.uuid4(),
            program_id=uuid.uuid4(),
            advancement_level=AdvancementLevel.APPRENTICE,
            status=EnrollmentStatus.ACTIVE,
            total_hours_logged=0.0,
        )
        assert enrollment.advancement_level == AdvancementLevel.APPRENTICE
        assert enrollment.total_hours_logged == 0.0

    def test_explicit_level_apprentice(self):
        enrollment = TrainingEnrollment(
            technician_id=uuid.uuid4(),
            program_id=uuid.uuid4(),
            advancement_level=AdvancementLevel.APPRENTICE,
        )
        assert enrollment.advancement_level == AdvancementLevel.APPRENTICE

    def test_tablename(self):
        assert TrainingEnrollment.__tablename__ == "training_enrollments"


class TestTrainingHoursLogModel:
    def test_create_instance(self):
        log = TrainingHoursLog(
            technician_id=uuid.uuid4(),
            hours=8.0,
            logged_date=date.today(),
            source=HoursLogSource.CLASSROOM,
            description="Day 1 of fiber splicing class",
            approved=False,
        )
        assert log.hours == 8.0
        assert log.source == HoursLogSource.CLASSROOM
        assert log.approved is False

    def test_tablename(self):
        assert TrainingHoursLog.__tablename__ == "training_hours_logs"


class TestAdvancementGateConfigModel:
    def test_create_instance(self):
        gate = AdvancementGateConfig(
            skill_id=uuid.uuid4(),
            target_level=AdvancementLevel.ADVANCED,
            certification_id=uuid.uuid4(),
            is_mandatory=True,
            gate_description="FOA CFOT required for advanced fiber splicing",
        )
        assert gate.target_level == AdvancementLevel.ADVANCED
        assert gate.is_mandatory is True

    def test_tablename(self):
        assert AdvancementGateConfig.__tablename__ == "advancement_gate_configs"


# ---------------------------------------------------------------------------
# Schema Validation Tests
# ---------------------------------------------------------------------------

class TestTrainingSchemas:
    def test_program_create(self):
        schema = TrainingProgramCreate(
            name="Test", slug="test",
            intermediate_hours_threshold=100.0,
            advanced_hours_threshold=300.0,
        )
        assert schema.name == "Test"

    def test_enrollment_create(self):
        tech_id = uuid.uuid4()
        prog_id = uuid.uuid4()
        schema = TrainingEnrollmentCreate(
            technician_id=tech_id,
            program_id=prog_id,
        )
        assert schema.advancement_level == AdvancementLevel.APPRENTICE
        assert schema.status == EnrollmentStatus.ACTIVE

    def test_hours_log_create_requires_positive_hours(self):
        with pytest.raises(Exception):
            TrainingHoursLogCreate(
                technician_id=uuid.uuid4(),
                hours=-5.0,
                logged_date=date.today(),
            )

    def test_hours_log_create_valid(self):
        schema = TrainingHoursLogCreate(
            technician_id=uuid.uuid4(),
            hours=4.5,
            logged_date=date.today(),
            source=HoursLogSource.FIELD_TRAINING,
        )
        assert schema.hours == 4.5

    def test_gate_config_create(self):
        schema = AdvancementGateConfigCreate(
            target_level=AdvancementLevel.INTERMEDIATE,
            certification_id=uuid.uuid4(),
        )
        assert schema.is_mandatory is True

    def test_training_summary(self):
        summary = TechnicianTrainingSummary(
            technician_id=uuid.uuid4(),
            technician_name="John Doe",
            total_training_hours=250.0,
            active_enrollments=2,
            completed_enrollments=0,
            highest_advancement_level=AdvancementLevel.INTERMEDIATE,
        )
        assert summary.total_training_hours == 250.0


# ---------------------------------------------------------------------------
# Seed Data Tests
# ---------------------------------------------------------------------------

class TestTrainingSeedData:
    def test_five_training_programs(self):
        assert len(TRAINING_PROGRAMS) == 5

    def test_all_programs_have_required_fields(self):
        required = {"id", "name", "slug", "description", "intermediate_hours_threshold",
                     "advanced_hours_threshold", "skill_category_id"}
        for prog in TRAINING_PROGRAMS:
            missing = required - set(prog.keys())
            assert not missing, f"Program '{prog.get('name')}' missing: {missing}"

    def test_gate_configs_reference_real_certs(self):
        from app.seeds.skills_and_certs import CERTIFICATIONS
        cert_ids = {c["id"] for c in CERTIFICATIONS}
        for gate in ADVANCEMENT_GATE_CONFIGS:
            assert gate["certification_id"] in cert_ids, (
                f"Gate config references unknown cert: {gate['certification_id']}"
            )

    def test_gate_configs_reference_real_programs(self):
        prog_ids = {p["id"] for p in TRAINING_PROGRAMS}
        for gate in ADVANCEMENT_GATE_CONFIGS:
            if gate.get("program_id"):
                assert gate["program_id"] in prog_ids, (
                    f"Gate config references unknown program: {gate['program_id']}"
                )

    def test_gate_configs_have_valid_target_levels(self):
        valid_levels = {"Apprentice", "Intermediate", "Advanced"}
        for gate in ADVANCEMENT_GATE_CONFIGS:
            assert gate["target_level"] in valid_levels

    def test_at_least_five_gate_configs(self):
        """Should have cert gates for known skill/cert combos."""
        assert len(ADVANCEMENT_GATE_CONFIGS) >= 5
