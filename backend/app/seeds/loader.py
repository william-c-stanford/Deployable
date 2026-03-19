"""Seed data loader — inserts taxonomy, technician, and project records into the database.

Usage:
    from app.seeds.loader import seed_all
    seed_all(db_session)
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from app.models.technician import (
    Technician,
    TechnicianSkill,
    TechnicianCertification,
    TechnicianDocument,
    TechnicianBadge,
)
from app.seeds.skills_and_certs import seed_skills_and_certifications
from app.seeds.technicians import TECHNICIANS
from app.seeds.training_programs import seed_training_programs
from app.seeds.projects import seed_projects

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _parse_date(val: str | None) -> date | None:
    if val is None:
        return None
    return date.fromisoformat(val)


def seed_technicians(db: Session) -> None:
    """Seed all 55 technicians with skills, certs, documents, and badges."""
    for tech_data in TECHNICIANS:
        # Check if technician already exists
        exists = db.query(Technician).filter_by(email=tech_data["email"]).first()
        if exists:
            continue

        # Extract nested collections
        skills_data = tech_data.get("skills", [])
        certs_data = tech_data.get("certifications", [])
        docs_data = tech_data.get("documents", [])
        badges_data = tech_data.get("site_badges", [])

        # Build technician record (exclude nested collections)
        tech_fields = {
            k: v
            for k, v in tech_data.items()
            if k not in ("skills", "certifications", "documents", "site_badges")
        }

        # Parse date fields
        for date_field in ("hire_date", "available_from"):
            if date_field in tech_fields and isinstance(tech_fields[date_field], str):
                tech_fields[date_field] = _parse_date(tech_fields[date_field])

        # Handle deployability_locked default
        tech_fields.setdefault("deployability_locked", False)

        tech = Technician(**tech_fields)
        db.add(tech)
        db.flush()  # get the tech.id

        # Skills
        for s in skills_data:
            db.add(
                TechnicianSkill(
                    technician_id=tech.id,
                    skill_name=s["skill_name"],
                    proficiency_level=s["proficiency_level"],
                    training_hours_accumulated=s["training_hours_accumulated"],
                    last_used_date=_parse_date(s.get("last_used_date")),
                )
            )

        # Certifications
        for c in certs_data:
            db.add(
                TechnicianCertification(
                    technician_id=tech.id,
                    cert_name=c["cert_name"],
                    issue_date=_parse_date(c.get("issue_date")),
                    expiry_date=_parse_date(c.get("expiry_date")),
                    status=c.get("status", "Active"),
                    credential_number=c.get("credential_number"),
                )
            )

        # Documents
        for d in docs_data:
            db.add(
                TechnicianDocument(
                    technician_id=tech.id,
                    doc_type=d["doc_type"],
                    verification_status=d["verification_status"],
                )
            )

        # Badges
        for b in badges_data:
            db.add(
                TechnicianBadge(
                    technician_id=tech.id,
                    badge_name=b["badge_name"],
                    badge_type=b.get("badge_type", "site"),
                )
            )

    db.flush()


def seed_all(db: Session) -> None:
    """Run all seed operations in order.

    1. Taxonomy (skills & certifications) via skills_and_certs module
    2. Training programs and advancement gate configs
    3. Technicians with all related records
    4. Partners, projects, and project roles
    """
    seed_skills_and_certifications(db)
    seed_training_programs(db)
    seed_technicians(db)
    seed_projects(db)
    db.commit()
