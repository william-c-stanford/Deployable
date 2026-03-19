"""Seed data for training programs and advancement gate configurations.

Creates 5 training programs (one per skill category) with realistic
thresholds and cert gate requirements matching the skills taxonomy.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.seeds.skills_and_certs import _uuid, _cat_id, CERTIFICATIONS


# ---------------------------------------------------------------------------
# Deterministic UUIDs for training programs
# ---------------------------------------------------------------------------

def _prog_uuid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"deployable.program.{name}"))


# Cert lookup by name
_cert_by_name = {c["name"]: c["id"] for c in CERTIFICATIONS}


# ===================================================================
# TRAINING PROGRAMS (5 programs, one per skill category)
# ===================================================================

TRAINING_PROGRAMS: list[dict[str, Any]] = [
    {
        "id": _prog_uuid("fiber_optic_foundations"),
        "name": "Fiber Optic Foundations",
        "slug": "fiber-optic-foundations",
        "description": (
            "Comprehensive fiber optic training program covering splicing, "
            "termination, testing, and PON systems. Designed to take technicians "
            "from apprentice to advanced fiber optic professionals."
        ),
        "total_hours_required": 600.0,
        "apprentice_hours_min": 0.0,
        "intermediate_hours_threshold": 100.0,
        "advanced_hours_threshold": 300.0,
        "skill_category_id": _cat_id["Fiber Optic"],
        "is_active": True,
        "display_order": 1,
    },
    {
        "id": _prog_uuid("structured_cabling_pro"),
        "name": "Structured Cabling Professional",
        "slug": "structured-cabling-pro",
        "description": (
            "Copper and hybrid cabling infrastructure training covering "
            "termination, certification testing, and pathway installation "
            "per TIA/EIA and BICSI standards."
        ),
        "total_hours_required": 500.0,
        "apprentice_hours_min": 0.0,
        "intermediate_hours_threshold": 80.0,
        "advanced_hours_threshold": 250.0,
        "skill_category_id": _cat_id["Structured Cabling"],
        "is_active": True,
        "display_order": 2,
    },
    {
        "id": _prog_uuid("data_center_infrastructure"),
        "name": "Data Center Infrastructure",
        "slug": "data-center-infrastructure",
        "description": (
            "Data center focused training covering rack installation, "
            "power systems, cooling management, and high-density fiber patching."
        ),
        "total_hours_required": 600.0,
        "apprentice_hours_min": 0.0,
        "intermediate_hours_threshold": 100.0,
        "advanced_hours_threshold": 300.0,
        "skill_category_id": _cat_id["Data Center"],
        "is_active": True,
        "display_order": 3,
    },
    {
        "id": _prog_uuid("aerial_underground_construction"),
        "name": "Aerial & Underground Construction",
        "slug": "aerial-underground-construction",
        "description": (
            "OSP construction training covering pole climbing, aerial "
            "strand/lashing, underground trenching, directional boring, "
            "and bucket truck operations."
        ),
        "total_hours_required": 700.0,
        "apprentice_hours_min": 0.0,
        "intermediate_hours_threshold": 120.0,
        "advanced_hours_threshold": 350.0,
        "skill_category_id": _cat_id["Aerial & Underground"],
        "is_active": True,
        "display_order": 4,
    },
    {
        "id": _prog_uuid("general_telecom_safety"),
        "name": "General Telecom & Safety",
        "slug": "general-telecom-safety",
        "description": (
            "Cross-cutting training covering site surveys, documentation, "
            "OSHA safety compliance, confined space entry, and job hazard analysis."
        ),
        "total_hours_required": 300.0,
        "apprentice_hours_min": 0.0,
        "intermediate_hours_threshold": 60.0,
        "advanced_hours_threshold": 200.0,
        "skill_category_id": _cat_id["General Telecom"],
        "is_active": True,
        "display_order": 5,
    },
]


# ===================================================================
# ADVANCEMENT GATE CONFIGS
# Maps cert gates from skills taxonomy into formal AdvancementGateConfig rows
# ===================================================================

ADVANCEMENT_GATE_CONFIGS: list[dict[str, Any]] = [
    # Fiber Splicing → Advanced requires FOA CFOT
    {
        "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "deployable.gate.fiber_splicing.advanced.foa_cfot")),
        "program_id": _prog_uuid("fiber_optic_foundations"),
        "skill_id": _uuid("skill", "fiber_splicing"),
        "target_level": "Advanced",
        "certification_id": _cert_by_name["FOA CFOT"],
        "is_mandatory": True,
        "gate_description": "FOA CFOT required for Advanced fiber splicing",
    },
    # OTDR Testing → Intermediate requires FOA CFOT
    {
        "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "deployable.gate.otdr_testing.intermediate.foa_cfot")),
        "program_id": _prog_uuid("fiber_optic_foundations"),
        "skill_id": _uuid("skill", "otdr_testing"),
        "target_level": "Intermediate",
        "certification_id": _cert_by_name["FOA CFOT"],
        "is_mandatory": True,
        "gate_description": "FOA CFOT required for Intermediate OTDR testing",
    },
    # High-Density Fiber Patching → Advanced requires FOA CFOT
    {
        "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "deployable.gate.high_density_fiber.advanced.foa_cfot")),
        "program_id": _prog_uuid("data_center_infrastructure"),
        "skill_id": _uuid("skill", "high_density_fiber"),
        "target_level": "Advanced",
        "certification_id": _cert_by_name["FOA CFOT"],
        "is_mandatory": True,
        "gate_description": "FOA CFOT required for Advanced high-density fiber patching",
    },
    # Copper Termination → Advanced requires BICSI Technician
    {
        "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "deployable.gate.copper_termination.advanced.bicsi")),
        "program_id": _prog_uuid("structured_cabling_pro"),
        "skill_id": _uuid("skill", "copper_termination"),
        "target_level": "Advanced",
        "certification_id": _cert_by_name["BICSI Technician"],
        "is_mandatory": True,
        "gate_description": "BICSI Technician certification required for Advanced copper termination",
    },
    # Safety & Compliance → Intermediate requires OSHA 10
    {
        "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "deployable.gate.safety.intermediate.osha10")),
        "program_id": _prog_uuid("general_telecom_safety"),
        "skill_id": _uuid("skill", "safety_compliance"),
        "target_level": "Intermediate",
        "certification_id": _cert_by_name["OSHA 10"],
        "is_mandatory": True,
        "gate_description": "OSHA 10 required for Intermediate safety & compliance",
    },
    # Safety & Compliance → Advanced requires OSHA 30
    {
        "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "deployable.gate.safety.advanced.osha30")),
        "program_id": _prog_uuid("general_telecom_safety"),
        "skill_id": _uuid("skill", "safety_compliance"),
        "target_level": "Advanced",
        "certification_id": _cert_by_name["OSHA 30"],
        "is_mandatory": True,
        "gate_description": "OSHA 30 required for Advanced safety & compliance",
    },
]


# ===================================================================
# Seeding helper
# ===================================================================

def seed_training_programs(session) -> dict[str, int]:
    """Insert training programs and advancement gate configs.

    Idempotent via merge (upsert by primary key).
    Returns counts of records upserted.
    """
    from app.models.training import TrainingProgram, AdvancementGateConfig, AdvancementLevel

    counts: dict[str, int] = {"training_programs": 0, "gate_configs": 0}

    # Map string level to enum
    level_map = {v.value: v for v in AdvancementLevel}

    for prog_data in TRAINING_PROGRAMS:
        obj = TrainingProgram(**prog_data)
        session.merge(obj)
        counts["training_programs"] += 1

    for gate_data in ADVANCEMENT_GATE_CONFIGS:
        data = dict(gate_data)
        data["target_level"] = level_map[data["target_level"]]
        obj = AdvancementGateConfig(**data)
        session.merge(obj)
        counts["gate_configs"] += 1

    session.commit()
    return counts
