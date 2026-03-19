"""Seed data for the skills taxonomy and industry-standard certifications.

Defines 18 skills across 5 categories and 14 certifications covering
industry, safety, vendor, and government cert types.
"""

from __future__ import annotations

import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Deterministic UUIDs so foreign-key references are stable across seed runs
# ---------------------------------------------------------------------------

def _uuid(ns: str, name: str) -> str:
    """Generate a deterministic UUID-5 from a namespace string and name."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"deployable.{ns}.{name}"))


# ===================================================================
# SKILL CATEGORIES
# ===================================================================

SKILL_CATEGORIES: list[dict[str, Any]] = [
    {
        "id": _uuid("skill_cat", "fiber_optic"),
        "name": "Fiber Optic",
        "description": "Skills related to fiber optic cable installation, splicing, testing, and maintenance",
        "display_order": 1,
    },
    {
        "id": _uuid("skill_cat", "structured_cabling"),
        "name": "Structured Cabling",
        "description": "Copper and hybrid cabling infrastructure skills",
        "display_order": 2,
    },
    {
        "id": _uuid("skill_cat", "data_center"),
        "name": "Data Center",
        "description": "Data center infrastructure installation, power, and cooling skills",
        "display_order": 3,
    },
    {
        "id": _uuid("skill_cat", "aerial_underground"),
        "name": "Aerial & Underground",
        "description": "Pole-line aerial and underground conduit construction skills",
        "display_order": 4,
    },
    {
        "id": _uuid("skill_cat", "general_telecom"),
        "name": "General Telecom",
        "description": "Cross-cutting telecommunications and safety skills",
        "display_order": 5,
    },
]

# Helper look-ups
_cat_id = {c["name"]: c["id"] for c in SKILL_CATEGORIES}

# ===================================================================
# SKILLS TAXONOMY  (18 skills)
# ===================================================================

SKILLS: list[dict[str, Any]] = [
    # ---- Fiber Optic (6 skills) ----
    {
        "id": _uuid("skill", "fiber_splicing"),
        "name": "Fiber Splicing",
        "slug": "fiber-splicing",
        "description": "Fusion and mechanical splicing of single-mode and multi-mode fiber strands",
        "category_id": _cat_id["Fiber Optic"],
        "intermediate_hours_threshold": 100,
        "advanced_hours_threshold": 300,
        "cert_gate_advanced": "FOA CFOT",
        "display_order": 1,
    },
    {
        "id": _uuid("skill", "otdr_testing"),
        "name": "OTDR Testing",
        "slug": "otdr-testing",
        "description": "Optical Time-Domain Reflectometer testing, trace analysis, and fault localization",
        "category_id": _cat_id["Fiber Optic"],
        "intermediate_hours_threshold": 80,
        "advanced_hours_threshold": 250,
        "cert_gate_intermediate": "FOA CFOT",
        "display_order": 2,
    },
    {
        "id": _uuid("skill", "fiber_termination"),
        "name": "Fiber Termination",
        "slug": "fiber-termination",
        "description": "Connectorization of fiber ends using epoxy/polish and pre-polished methods (SC, LC, MPO)",
        "category_id": _cat_id["Fiber Optic"],
        "intermediate_hours_threshold": 100,
        "advanced_hours_threshold": 300,
        "display_order": 3,
    },
    {
        "id": _uuid("skill", "fiber_cable_pulling"),
        "name": "Fiber Cable Pulling",
        "slug": "fiber-cable-pulling",
        "description": "Pulling fiber optic cables through conduit, innerduct, and cable trays with proper tension management",
        "category_id": _cat_id["Fiber Optic"],
        "intermediate_hours_threshold": 80,
        "advanced_hours_threshold": 250,
        "display_order": 4,
    },
    {
        "id": _uuid("skill", "fiber_route_design"),
        "name": "Fiber Route Design",
        "slug": "fiber-route-design",
        "description": "Reading and interpreting fiber route engineering drawings and splice schematics",
        "category_id": _cat_id["Fiber Optic"],
        "intermediate_hours_threshold": 120,
        "advanced_hours_threshold": 350,
        "display_order": 5,
    },
    {
        "id": _uuid("skill", "pon_systems"),
        "name": "PON Systems",
        "slug": "pon-systems",
        "description": "GPON/XGS-PON passive optical network deployment, OLT/ONT installation and provisioning",
        "category_id": _cat_id["Fiber Optic"],
        "intermediate_hours_threshold": 100,
        "advanced_hours_threshold": 300,
        "display_order": 6,
    },
    # ---- Structured Cabling (3 skills) ----
    {
        "id": _uuid("skill", "copper_termination"),
        "name": "Copper Termination",
        "slug": "copper-termination",
        "description": "Cat5e/Cat6/Cat6A cable termination, patch panel punchdown, and testing",
        "category_id": _cat_id["Structured Cabling"],
        "intermediate_hours_threshold": 80,
        "advanced_hours_threshold": 250,
        "cert_gate_advanced": "BICSI Technician",
        "display_order": 1,
    },
    {
        "id": _uuid("skill", "cable_certification"),
        "name": "Cable Certification Testing",
        "slug": "cable-certification-testing",
        "description": "Operating Fluke or equivalent testers for TIA/EIA cable certification and reporting",
        "category_id": _cat_id["Structured Cabling"],
        "intermediate_hours_threshold": 60,
        "advanced_hours_threshold": 200,
        "display_order": 2,
    },
    {
        "id": _uuid("skill", "pathway_routing"),
        "name": "Pathway & Conduit Routing",
        "slug": "pathway-conduit-routing",
        "description": "Cable tray, J-hook, conduit, and innerduct installation per NEC/BICSI standards",
        "category_id": _cat_id["Structured Cabling"],
        "intermediate_hours_threshold": 80,
        "advanced_hours_threshold": 250,
        "display_order": 3,
    },
    # ---- Data Center (4 skills) ----
    {
        "id": _uuid("skill", "rack_mounting"),
        "name": "Rack & Cabinet Mounting",
        "slug": "rack-cabinet-mounting",
        "description": "Server rack assembly, equipment mounting, labeling, and cable management in data center environments",
        "category_id": _cat_id["Data Center"],
        "intermediate_hours_threshold": 80,
        "advanced_hours_threshold": 250,
        "display_order": 1,
    },
    {
        "id": _uuid("skill", "dc_power"),
        "name": "DC Power Systems",
        "slug": "dc-power-systems",
        "description": "DC power plant installation, battery backup systems, bus-bar connections, and grounding/bonding",
        "category_id": _cat_id["Data Center"],
        "intermediate_hours_threshold": 120,
        "advanced_hours_threshold": 350,
        "display_order": 2,
    },
    {
        "id": _uuid("skill", "hot_aisle_containment"),
        "name": "Hot/Cold Aisle Containment",
        "slug": "hot-cold-aisle-containment",
        "description": "Airflow management, blanking panel installation, and containment system setup",
        "category_id": _cat_id["Data Center"],
        "intermediate_hours_threshold": 60,
        "advanced_hours_threshold": 200,
        "display_order": 3,
    },
    {
        "id": _uuid("skill", "high_density_fiber"),
        "name": "High-Density Fiber Patching",
        "slug": "high-density-fiber-patching",
        "description": "MTP/MPO trunk installation, cassette-based patching, and high-count fiber management in DCs",
        "category_id": _cat_id["Data Center"],
        "intermediate_hours_threshold": 100,
        "advanced_hours_threshold": 300,
        "cert_gate_advanced": "FOA CFOT",
        "display_order": 4,
    },
    # ---- Aerial & Underground (3 skills) ----
    {
        "id": _uuid("skill", "aerial_construction"),
        "name": "Aerial Construction",
        "slug": "aerial-construction",
        "description": "Pole climbing, strand installation, lashing, and aerial fiber/copper placement",
        "category_id": _cat_id["Aerial & Underground"],
        "intermediate_hours_threshold": 120,
        "advanced_hours_threshold": 350,
        "display_order": 1,
    },
    {
        "id": _uuid("skill", "underground_construction"),
        "name": "Underground Construction",
        "slug": "underground-construction",
        "description": "Trenching, directional boring, conduit placement, and vault/manhole work",
        "category_id": _cat_id["Aerial & Underground"],
        "intermediate_hours_threshold": 120,
        "advanced_hours_threshold": 350,
        "display_order": 2,
    },
    {
        "id": _uuid("skill", "bucket_truck_ops"),
        "name": "Bucket Truck Operations",
        "slug": "bucket-truck-operations",
        "description": "Safe operation of aerial lift equipment for overhead cable installation and maintenance",
        "category_id": _cat_id["Aerial & Underground"],
        "intermediate_hours_threshold": 80,
        "advanced_hours_threshold": 250,
        "display_order": 3,
    },
    # ---- General Telecom (2 skills) ----
    {
        "id": _uuid("skill", "site_survey"),
        "name": "Site Survey & Documentation",
        "slug": "site-survey-documentation",
        "description": "Pre-construction site surveys, as-built documentation, photo logging, and redlining",
        "category_id": _cat_id["General Telecom"],
        "intermediate_hours_threshold": 60,
        "advanced_hours_threshold": 200,
        "display_order": 1,
    },
    {
        "id": _uuid("skill", "safety_compliance"),
        "name": "Safety & Compliance",
        "slug": "safety-compliance",
        "description": "OSHA standards, confined space entry, lockout/tagout, PPE, and job hazard analysis",
        "category_id": _cat_id["General Telecom"],
        "intermediate_hours_threshold": 40,
        "advanced_hours_threshold": 150,
        "cert_gate_intermediate": "OSHA 10",
        "cert_gate_advanced": "OSHA 30",
        "display_order": 2,
    },
]


# ===================================================================
# CERTIFICATIONS  (14 certs)
# ===================================================================

CERTIFICATIONS: list[dict[str, Any]] = [
    # ---- Industry Certifications ----
    {
        "id": _uuid("cert", "foa_cfot"),
        "name": "FOA CFOT",
        "slug": "foa-cfot",
        "issuing_body": "Fiber Optic Association",
        "description": "Certified Fiber Optic Technician — foundational credential for fiber optic installation and testing",
        "validity_months": 0,  # Does not expire
        "cert_category": "industry",
        "display_order": 1,
    },
    {
        "id": _uuid("cert", "foa_cfos_s"),
        "name": "FOA CFOS/S",
        "slug": "foa-cfos-s",
        "issuing_body": "Fiber Optic Association",
        "description": "Certified Fiber Optic Specialist in Splicing — advanced splicing credential",
        "validity_months": 0,
        "cert_category": "industry",
        "display_order": 2,
    },
    {
        "id": _uuid("cert", "foa_cfos_t"),
        "name": "FOA CFOS/T",
        "slug": "foa-cfos-t",
        "issuing_body": "Fiber Optic Association",
        "description": "Certified Fiber Optic Specialist in Testing — advanced OTDR and testing credential",
        "validity_months": 0,
        "cert_category": "industry",
        "display_order": 3,
    },
    {
        "id": _uuid("cert", "bicsi_technician"),
        "name": "BICSI Technician",
        "slug": "bicsi-technician",
        "issuing_body": "BICSI",
        "description": "BICSI Installer 1 or Installer 2 — cabling infrastructure installation credential",
        "validity_months": 36,  # 3-year renewal
        "cert_category": "industry",
        "display_order": 4,
    },
    {
        "id": _uuid("cert", "bicsi_rcdd"),
        "name": "BICSI RCDD",
        "slug": "bicsi-rcdd",
        "issuing_body": "BICSI",
        "description": "Registered Communications Distribution Designer — advanced design credential",
        "validity_months": 36,
        "cert_category": "industry",
        "display_order": 5,
    },
    {
        "id": _uuid("cert", "eia_tia_structured"),
        "name": "ETA Fiber Optics Installer",
        "slug": "eta-fiber-optics-installer",
        "issuing_body": "ETA International",
        "description": "Fiber optics installer certification covering standards-compliant installation practices",
        "validity_months": 24,
        "cert_category": "industry",
        "display_order": 6,
    },
    # ---- Safety Certifications ----
    {
        "id": _uuid("cert", "osha_10"),
        "name": "OSHA 10",
        "slug": "osha-10",
        "issuing_body": "OSHA / Authorized Trainers",
        "description": "10-hour OSHA Outreach Training — construction industry safety fundamentals",
        "validity_months": 60,  # Recommended 5-year refresh
        "cert_category": "safety",
        "display_order": 7,
    },
    {
        "id": _uuid("cert", "osha_30"),
        "name": "OSHA 30",
        "slug": "osha-30",
        "issuing_body": "OSHA / Authorized Trainers",
        "description": "30-hour OSHA Outreach Training — comprehensive construction safety for supervisors",
        "validity_months": 60,
        "cert_category": "safety",
        "display_order": 8,
    },
    {
        "id": _uuid("cert", "first_aid_cpr"),
        "name": "First Aid/CPR/AED",
        "slug": "first-aid-cpr-aed",
        "issuing_body": "American Red Cross / AHA",
        "description": "Workplace first aid, CPR, and AED certification",
        "validity_months": 24,
        "cert_category": "safety",
        "display_order": 9,
    },
    {
        "id": _uuid("cert", "confined_space"),
        "name": "Confined Space Entry",
        "slug": "confined-space-entry",
        "issuing_body": "Various OSHA-Accredited Providers",
        "description": "Permit-required confined space entry training for manhole and vault work",
        "validity_months": 12,
        "cert_category": "safety",
        "display_order": 10,
    },
    # ---- Vendor Certifications ----
    {
        "id": _uuid("cert", "corning_certified"),
        "name": "Corning Certified Installer",
        "slug": "corning-certified-installer",
        "issuing_body": "Corning Inc.",
        "description": "Vendor-specific certification for Corning fiber infrastructure products",
        "validity_months": 24,
        "cert_category": "vendor",
        "display_order": 11,
    },
    {
        "id": _uuid("cert", "commscope_certified"),
        "name": "CommScope PartnerPRO Installer",
        "slug": "commscope-partnerpro-installer",
        "issuing_body": "CommScope",
        "description": "Vendor-specific certification for CommScope/SYSTIMAX structured cabling systems",
        "validity_months": 24,
        "cert_category": "vendor",
        "display_order": 12,
    },
    # ---- Government / Compliance ----
    {
        "id": _uuid("cert", "cdl_class_a"),
        "name": "CDL Class A",
        "slug": "cdl-class-a",
        "issuing_body": "State DMV",
        "description": "Commercial Driver's License Class A — required for operating heavy equipment trailers and cable trucks",
        "validity_months": 48,
        "cert_category": "government",
        "display_order": 13,
    },
    {
        "id": _uuid("cert", "flagger_cert"),
        "name": "Flagger Certification",
        "slug": "flagger-certification",
        "issuing_body": "ATSSA / State DOT",
        "description": "Traffic control flagger certification for roadway and right-of-way work zones",
        "validity_months": 36,
        "cert_category": "government",
        "display_order": 14,
    },
]


# ===================================================================
# Seeding helper
# ===================================================================

def seed_skills_and_certifications(session) -> dict[str, int]:
    """Insert skill categories, skills, and certifications into the database.

    Uses merge (upsert by primary key) so the seeder is idempotent.
    Returns counts of records upserted.
    """
    from app.models.skill import SkillCategory, Skill
    from app.models.certification import Certification

    counts: dict[str, int] = {"skill_categories": 0, "skills": 0, "certifications": 0}

    for cat_data in SKILL_CATEGORIES:
        obj = SkillCategory(**cat_data)
        session.merge(obj)
        counts["skill_categories"] += 1

    for skill_data in SKILL_CATEGORIES:
        pass  # categories already handled

    for skill_data in SKILLS:
        obj = Skill(**skill_data)
        session.merge(obj)
        counts["skills"] += 1

    for cert_data in CERTIFICATIONS:
        obj = Certification(**cert_data)
        session.merge(obj)
        counts["certifications"] += 1

    session.commit()
    return counts
