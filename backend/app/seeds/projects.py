"""Seed data for partners, projects, and project roles.

Mirrors the mock data in frontend/src/lib/mockProjects.ts so that
ProjectStaffing renders real database-backed data instead of hardcoded mocks.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from app.models.user import Partner
from app.models.project import Project, ProjectRole, ProjectStatus

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Partner definitions
# ---------------------------------------------------------------------------

PARTNERS = [
    {
        "name": "Lumen Technologies",
        "contact_email": "projects@lumen.com",
        "contact_phone": "800-555-0101",
    },
    {
        "name": "Equinix",
        "contact_email": "staffing@equinix.com",
        "contact_phone": "800-555-0102",
    },
    {
        "name": "AT&T",
        "contact_email": "fiber-ops@att.com",
        "contact_phone": "800-555-0103",
    },
    {
        "name": "Crown Castle",
        "contact_email": "deployments@crowncastle.com",
        "contact_phone": "800-555-0104",
    },
    {
        "name": "Zayo Group",
        "contact_email": "network-ops@zayo.com",
        "contact_phone": "800-555-0105",
    },
]


# ---------------------------------------------------------------------------
# Project definitions (with nested roles)
# ---------------------------------------------------------------------------

PROJECTS = [
    {
        "name": "Metro Fiber Expansion - Phoenix",
        "partner_name": "Lumen Technologies",
        "status": "Active",
        "location_region": "Arizona",
        "location_city": "Phoenix",
        "start_date": "2026-01-15",
        "end_date": "2026-06-30",
        "budget_hours": 12000,
        "description": (
            "Large-scale metro fiber deployment covering downtown Phoenix and "
            "surrounding suburbs. Includes aerial and underground installation "
            "across 45 miles."
        ),
        "roles": [
            {
                "role_name": "Lead Splicer",
                "required_skills": [
                    {"skill": "Fiber Splicing", "min_level": "Advanced"},
                    {"skill": "OTDR Testing", "min_level": "Intermediate"},
                ],
                "required_certs": ["CFOT", "OSHA 10"],
                "skill_weights": {"Fiber Splicing": 0.6, "OTDR Testing": 0.3, "Safety": 0.1},
                "quantity": 2,
                "filled": 2,
                "hourly_rate": 55.0,
                "per_diem": 85.0,
            },
            {
                "role_name": "Fiber Technician",
                "required_skills": [
                    {"skill": "Fiber Splicing", "min_level": "Intermediate"},
                    {"skill": "Cable Pulling", "min_level": "Beginner"},
                ],
                "required_certs": ["OSHA 10"],
                "skill_weights": {"Fiber Splicing": 0.5, "Cable Pulling": 0.4, "Safety": 0.1},
                "quantity": 4,
                "filled": 3,
                "hourly_rate": 42.0,
                "per_diem": 75.0,
            },
            {
                "role_name": "OTDR Tester",
                "required_skills": [
                    {"skill": "OTDR Testing", "min_level": "Intermediate"},
                    {"skill": "Fiber Splicing", "min_level": "Beginner"},
                ],
                "required_certs": ["CFOT"],
                "skill_weights": {"OTDR Testing": 0.7, "Fiber Splicing": 0.2, "Safety": 0.1},
                "quantity": 2,
                "filled": 1,
                "hourly_rate": 48.0,
                "per_diem": 75.0,
            },
        ],
    },
    {
        "name": "Data Center Cabling - Dallas",
        "partner_name": "Equinix",
        "status": "Staffing",
        "location_region": "Texas",
        "location_city": "Dallas",
        "start_date": "2026-04-01",
        "end_date": "2026-09-15",
        "budget_hours": 18000,
        "description": (
            "Structured cabling installation for new 50MW data center facility. "
            "Includes fiber backbone, copper distribution, and cable management systems."
        ),
        "roles": [
            {
                "role_name": "Senior Cable Tech",
                "required_skills": [
                    {"skill": "Structured Cabling", "min_level": "Advanced"},
                    {"skill": "Cable Management", "min_level": "Intermediate"},
                ],
                "required_certs": ["BICSI RCDD", "OSHA 30"],
                "skill_weights": {"Structured Cabling": 0.5, "Cable Management": 0.3, "Safety": 0.2},
                "quantity": 3,
                "filled": 1,
                "hourly_rate": 52.0,
                "per_diem": 90.0,
            },
            {
                "role_name": "Fiber Installer",
                "required_skills": [
                    {"skill": "Fiber Splicing", "min_level": "Intermediate"},
                    {"skill": "Cable Pulling", "min_level": "Intermediate"},
                ],
                "required_certs": ["CFOT", "OSHA 10"],
                "skill_weights": {"Fiber Splicing": 0.4, "Cable Pulling": 0.4, "Safety": 0.2},
                "quantity": 6,
                "filled": 2,
                "hourly_rate": 44.0,
                "per_diem": 80.0,
            },
            {
                "role_name": "Cable Puller",
                "required_skills": [
                    {"skill": "Cable Pulling", "min_level": "Beginner"},
                ],
                "required_certs": ["OSHA 10"],
                "skill_weights": {"Cable Pulling": 0.7, "Safety": 0.3},
                "quantity": 8,
                "filled": 0,
                "hourly_rate": 32.0,
                "per_diem": 65.0,
            },
        ],
    },
    {
        "name": "FTTH Rollout - Charlotte",
        "partner_name": "AT&T",
        "status": "Active",
        "location_region": "North Carolina",
        "location_city": "Charlotte",
        "start_date": "2025-11-01",
        "end_date": "2026-05-31",
        "budget_hours": 15000,
        "description": (
            "Fiber-to-the-home deployment in suburban Charlotte neighborhoods. "
            "Installation of ONTs, drop cables, and customer premises equipment."
        ),
        "roles": [
            {
                "role_name": "FTTH Installer",
                "required_skills": [
                    {"skill": "FTTH Installation", "min_level": "Intermediate"},
                    {"skill": "Fiber Splicing", "min_level": "Beginner"},
                ],
                "required_certs": ["CFOT", "OSHA 10"],
                "skill_weights": {"FTTH Installation": 0.6, "Fiber Splicing": 0.3, "Safety": 0.1},
                "quantity": 5,
                "filled": 5,
                "hourly_rate": 40.0,
                "per_diem": 70.0,
            },
            {
                "role_name": "Field Supervisor",
                "required_skills": [
                    {"skill": "FTTH Installation", "min_level": "Advanced"},
                    {"skill": "OTDR Testing", "min_level": "Intermediate"},
                    {"skill": "Project Management", "min_level": "Intermediate"},
                ],
                "required_certs": ["CFOT", "OSHA 30"],
                "skill_weights": {
                    "FTTH Installation": 0.3,
                    "OTDR Testing": 0.2,
                    "Project Management": 0.4,
                    "Safety": 0.1,
                },
                "quantity": 1,
                "filled": 1,
                "hourly_rate": 58.0,
                "per_diem": 95.0,
            },
        ],
    },
    {
        "name": "OSP Backbone - Denver",
        "partner_name": "Lumen Technologies",
        "status": "Wrapping Up",
        "location_region": "Colorado",
        "location_city": "Denver",
        "start_date": "2025-08-01",
        "end_date": "2026-03-31",
        "budget_hours": 9000,
        "description": (
            "Outside plant fiber backbone construction along the I-25 corridor. "
            "Long-haul fiber deployment with splice closures and hand holes."
        ),
        "roles": [
            {
                "role_name": "OSP Splicer",
                "required_skills": [
                    {"skill": "Fiber Splicing", "min_level": "Advanced"},
                    {"skill": "Aerial Installation", "min_level": "Intermediate"},
                ],
                "required_certs": ["CFOT", "OSHA 30", "CDL Class B"],
                "skill_weights": {"Fiber Splicing": 0.5, "Aerial Installation": 0.3, "Safety": 0.2},
                "quantity": 3,
                "filled": 3,
                "hourly_rate": 58.0,
                "per_diem": 100.0,
            },
        ],
    },
    {
        "name": "5G Small Cell Deployment - Miami",
        "partner_name": "Crown Castle",
        "status": "Staffing",
        "location_region": "Florida",
        "location_city": "Miami",
        "start_date": "2026-05-01",
        "end_date": "2026-12-31",
        "budget_hours": 20000,
        "description": (
            "Small cell and DAS installation across downtown Miami. Includes "
            "pole-mounted fiber attachments and rooftop equipment installation."
        ),
        "roles": [
            {
                "role_name": "Small Cell Installer",
                "required_skills": [
                    {"skill": "Small Cell Installation", "min_level": "Intermediate"},
                    {"skill": "Aerial Installation", "min_level": "Intermediate"},
                ],
                "required_certs": ["OSHA 30", "RF Safety"],
                "skill_weights": {"Small Cell Installation": 0.5, "Aerial Installation": 0.3, "Safety": 0.2},
                "quantity": 4,
                "filled": 0,
                "hourly_rate": 48.0,
                "per_diem": 85.0,
            },
            {
                "role_name": "RF Engineer",
                "required_skills": [
                    {"skill": "RF Testing", "min_level": "Advanced"},
                    {"skill": "Small Cell Installation", "min_level": "Intermediate"},
                ],
                "required_certs": ["RF Safety", "OSHA 10"],
                "skill_weights": {"RF Testing": 0.6, "Small Cell Installation": 0.3, "Safety": 0.1},
                "quantity": 2,
                "filled": 0,
                "hourly_rate": 62.0,
                "per_diem": 95.0,
            },
        ],
    },
    {
        "name": "Campus Fiber Network - Austin",
        "partner_name": "Equinix",
        "status": "Draft",
        "location_region": "Texas",
        "location_city": "Austin",
        "start_date": "2026-07-01",
        "end_date": "2026-11-30",
        "budget_hours": 6000,
        "description": (
            "Enterprise campus fiber network installation for new corporate campus. "
            "Includes conduit, fiber plant, and termination in 5 buildings."
        ),
        "roles": [
            {
                "role_name": "Fiber Technician",
                "required_skills": [
                    {"skill": "Fiber Splicing", "min_level": "Intermediate"},
                    {"skill": "Structured Cabling", "min_level": "Beginner"},
                ],
                "required_certs": ["CFOT", "OSHA 10"],
                "skill_weights": {"Fiber Splicing": 0.5, "Structured Cabling": 0.4, "Safety": 0.1},
                "quantity": 3,
                "filled": 0,
                "hourly_rate": 44.0,
                "per_diem": 75.0,
            },
        ],
    },
    {
        "name": "Enterprise WAN Upgrade - Atlanta",
        "partner_name": "Zayo Group",
        "status": "Active",
        "location_region": "Georgia",
        "location_city": "Atlanta",
        "start_date": "2026-02-01",
        "end_date": "2026-08-31",
        "budget_hours": 10000,
        "description": (
            "Multi-site WAN upgrade for enterprise client. Fiber and copper "
            "infrastructure upgrades across 12 office locations in the Atlanta metro area."
        ),
        "roles": [
            {
                "role_name": "Network Technician",
                "required_skills": [
                    {"skill": "Structured Cabling", "min_level": "Intermediate"},
                    {"skill": "Fiber Splicing", "min_level": "Beginner"},
                ],
                "required_certs": ["BICSI Installer", "OSHA 10"],
                "skill_weights": {"Structured Cabling": 0.5, "Fiber Splicing": 0.3, "Safety": 0.2},
                "quantity": 4,
                "filled": 3,
                "hourly_rate": 42.0,
                "per_diem": 70.0,
            },
            {
                "role_name": "Project Lead",
                "required_skills": [
                    {"skill": "Project Management", "min_level": "Advanced"},
                    {"skill": "Structured Cabling", "min_level": "Intermediate"},
                ],
                "required_certs": ["BICSI RCDD", "OSHA 30"],
                "skill_weights": {"Project Management": 0.5, "Structured Cabling": 0.3, "Safety": 0.2},
                "quantity": 1,
                "filled": 1,
                "hourly_rate": 60.0,
                "per_diem": 95.0,
            },
        ],
    },
    {
        "name": "Municipal Fiber Build - Portland",
        "partner_name": "AT&T",
        "status": "Closed",
        "location_region": "Oregon",
        "location_city": "Portland",
        "start_date": "2025-03-01",
        "end_date": "2025-12-15",
        "budget_hours": 14000,
        "description": (
            "City-wide municipal broadband fiber network. Completed ahead of "
            "schedule with all fiber strands tested and documented."
        ),
        "roles": [
            {
                "role_name": "Fiber Splicer",
                "required_skills": [
                    {"skill": "Fiber Splicing", "min_level": "Intermediate"},
                ],
                "required_certs": ["CFOT"],
                "skill_weights": {"Fiber Splicing": 0.8, "Safety": 0.2},
                "quantity": 6,
                "filled": 6,
                "hourly_rate": 46.0,
                "per_diem": 80.0,
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Seeder function
# ---------------------------------------------------------------------------

def seed_projects(db: "Session") -> None:
    """Seed partners, projects, and project roles.

    Idempotent: skips partners/projects that already exist (matched by name).
    """

    # ── 1. Seed partners ─────────────────────────────────────────────────
    partner_map: dict[str, Partner] = {}

    for p_data in PARTNERS:
        partner = db.query(Partner).filter_by(name=p_data["name"]).first()
        if not partner:
            partner = Partner(**p_data)
            db.add(partner)
            db.flush()
        partner_map[partner.name] = partner

    # ── 2. Seed projects + roles ─────────────────────────────────────────
    for proj_data in PROJECTS:
        # Skip if project already seeded
        existing = db.query(Project).filter_by(name=proj_data["name"]).first()
        if existing:
            continue

        partner = partner_map[proj_data["partner_name"]]
        roles_data = proj_data.get("roles", [])

        project = Project(
            name=proj_data["name"],
            partner_id=partner.id,
            status=proj_data["status"],
            location_region=proj_data["location_region"],
            location_city=proj_data.get("location_city"),
            start_date=date.fromisoformat(proj_data["start_date"]),
            end_date=(
                date.fromisoformat(proj_data["end_date"])
                if proj_data.get("end_date")
                else None
            ),
            budget_hours=proj_data.get("budget_hours"),
            description=proj_data.get("description"),
        )
        db.add(project)
        db.flush()  # get project.id

        for role_data in roles_data:
            role = ProjectRole(
                project_id=project.id,
                role_name=role_data["role_name"],
                required_skills=role_data.get("required_skills", []),
                required_certs=role_data.get("required_certs", []),
                skill_weights=role_data.get("skill_weights", {}),
                quantity=role_data.get("quantity", 1),
                filled=role_data.get("filled", 0),
                hourly_rate=role_data.get("hourly_rate"),
                per_diem=role_data.get("per_diem"),
            )
            db.add(role)

    db.flush()
