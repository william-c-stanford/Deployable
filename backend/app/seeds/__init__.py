"""Seed data modules for Deployable."""

from app.seeds.skills_and_certs import (  # noqa: F401
    SKILL_CATEGORIES,
    SKILLS,
    CERTIFICATIONS,
    seed_skills_and_certifications,
)
from app.seeds.technicians import TECHNICIANS  # noqa: F401
from app.seeds.loader import seed_all, seed_technicians  # noqa: F401
from app.seeds.projects import seed_projects, PARTNERS, PROJECTS  # noqa: F401
