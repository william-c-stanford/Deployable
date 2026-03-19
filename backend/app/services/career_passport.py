"""Career Passport PDF generation service.

Compiles technician certifications, work history, skills, training enrollments,
and badges into a formatted PDF document using WeasyPrint (HTML→PDF).
Also provides data compilation for the shareable HTML page.
"""

import logging
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional
from uuid import UUID

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session, joinedload

from app.models.technician import (
    Technician,
    TechnicianCertification,
    TechnicianSkill,
    TechnicianBadge,
    CertStatus,
)
from app.models.assignment import Assignment
from app.models.project import Project, ProjectRole
from app.models.training import TrainingEnrollment, EnrollmentStatus

logger = logging.getLogger(__name__)

# Template directory
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=True,
)


def _load_technician(db: Session, technician_id: UUID) -> Optional[Technician]:
    """Load a technician with all relationships eagerly loaded."""
    return (
        db.query(Technician)
        .options(
            joinedload(Technician.skills),
            joinedload(Technician.certifications),
            joinedload(Technician.badges),
            joinedload(Technician.training_enrollments).joinedload(
                TrainingEnrollment.program
            ),
        )
        .filter(Technician.id == technician_id)
        .first()
    )


def _load_work_history(db: Session, technician_id: UUID) -> list[dict]:
    """Load assignment/project work history for the technician."""
    assignments = (
        db.query(Assignment)
        .options(
            joinedload(Assignment.role).joinedload(ProjectRole.project),
        )
        .filter(Assignment.technician_id == technician_id)
        .order_by(Assignment.start_date.desc())
        .all()
    )

    history = []
    for a in assignments:
        project = a.role.project if a.role else None
        history.append({
            "project_name": project.name if project else "Unknown Project",
            "role_name": a.role.role_name if a.role else "Technician",
            "location": (
                f"{project.location_city}, {project.location_region}"
                if project and project.location_city
                else (project.location_region if project else "—")
            ),
            "start_date": a.start_date,
            "end_date": a.end_date,
            "status": a.status or "Active",
            "partner_name": (
                project.partner.company_name
                if project and hasattr(project, "partner") and project.partner
                else None
            ),
        })
    return history


def compile_passport_data(
    db: Session,
    technician_id: UUID,
    *,
    expires_at: Optional[datetime] = None,
) -> Optional[dict]:
    """Compile all career passport data for a technician.

    Returns a dict suitable for rendering into HTML/PDF templates,
    or None if the technician is not found.
    """
    technician = _load_technician(db, technician_id)
    if not technician:
        return None

    # Sort skills by proficiency level (Advanced first) then name
    level_order = {"Advanced": 0, "Intermediate": 1, "Apprentice": 2}
    skills = sorted(
        technician.skills,
        key=lambda s: (
            level_order.get(
                s.proficiency_level.value if s.proficiency_level else "Apprentice", 3
            ),
            s.skill_name,
        ),
    )

    # Sort certifications: active first, then by name
    status_order = {"Active": 0, "Pending": 1, "Expired": 2, "Revoked": 3}
    certifications = sorted(
        technician.certifications,
        key=lambda c: (
            status_order.get(c.status.value if c.status else "Pending", 4),
            c.cert_name,
        ),
    )

    active_cert_count = sum(
        1 for c in certifications
        if c.status and c.status.value == "Active"
    )

    # Active training enrollments
    enrollments = [
        e for e in technician.training_enrollments
        if e.status == EnrollmentStatus.ACTIVE or e.status == EnrollmentStatus.COMPLETED
    ]
    enrollments.sort(
        key=lambda e: (
            0 if e.status == EnrollmentStatus.ACTIVE else 1,
            e.program.name if e.program else "",
        )
    )

    badges = sorted(
        technician.badges,
        key=lambda b: b.granted_at or datetime.min,
        reverse=True,
    )

    # Work history from assignments
    work_history = _load_work_history(db, technician_id)

    return {
        "technician": technician,
        "skills": skills,
        "certifications": certifications,
        "active_cert_count": active_cert_count,
        "enrollments": enrollments,
        "badges": badges,
        "work_history": work_history,
        "expires_at": expires_at,
        "generated_at": datetime.utcnow(),
    }


def render_passport_html(
    db: Session,
    technician_id: UUID,
    *,
    expires_at: Optional[datetime] = None,
) -> Optional[str]:
    """Render the career passport as an HTML string.

    Returns None if the technician is not found.
    """
    data = compile_passport_data(db, technician_id, expires_at=expires_at)
    if data is None:
        return None

    template = _jinja_env.get_template("career_passport.html")
    return template.render(**data)


def render_passport_pdf_html(
    db: Session,
    technician_id: UUID,
) -> Optional[str]:
    """Render the career passport as an HTML string optimized for PDF output.

    Uses the PDF-specific template with print-friendly styles.
    Returns None if the technician is not found.
    """
    data = compile_passport_data(db, technician_id)
    if data is None:
        return None

    template = _jinja_env.get_template("career_passport_pdf.html")
    return template.render(**data)


def generate_passport_pdf(
    db: Session,
    technician_id: UUID,
) -> Optional[tuple[bytes, str]]:
    """Generate a career passport PDF for a technician.

    Returns a tuple of (pdf_bytes, filename) or None if technician not found.
    Uses WeasyPrint for HTML→PDF conversion.
    """
    html_content = render_passport_pdf_html(db, technician_id)
    if html_content is None:
        return None

    try:
        from weasyprint import HTML

        pdf_buffer = BytesIO()
        HTML(string=html_content).write_pdf(pdf_buffer)
        pdf_bytes = pdf_buffer.getvalue()
    except ImportError:
        # Fallback: if WeasyPrint is not installed, generate a simpler PDF
        # using a basic approach
        logger.warning(
            "WeasyPrint not available, falling back to basic PDF generation"
        )
        pdf_bytes = _generate_fallback_pdf(db, technician_id)
        if pdf_bytes is None:
            return None
    except Exception as e:
        logger.error("Failed to generate PDF with WeasyPrint: %s", e)
        # Fallback to basic PDF
        pdf_bytes = _generate_fallback_pdf(db, technician_id)
        if pdf_bytes is None:
            return None

    # Build filename
    technician = _load_technician(db, technician_id)
    if technician:
        safe_name = (
            f"{technician.first_name}_{technician.last_name}"
            .replace(" ", "_")
            .replace("/", "_")
        )
        filename = f"career_passport_{safe_name}.pdf"
    else:
        filename = f"career_passport_{technician_id}.pdf"

    return pdf_bytes, filename


def _generate_fallback_pdf(db: Session, technician_id: UUID) -> Optional[bytes]:
    """Generate a basic PDF without WeasyPrint using reportlab or plain text.

    This is a fallback when WeasyPrint system dependencies aren't available.
    Generates a simple text-based PDF structure.
    """
    data = compile_passport_data(db, technician_id)
    if data is None:
        return None

    tech = data["technician"]

    # Minimal PDF structure (valid PDF 1.4)
    lines = []
    lines.append(f"CAREER PASSPORT — {tech.first_name} {tech.last_name}")
    lines.append(f"Generated by Deployable on {datetime.utcnow().strftime('%Y-%m-%d')}")
    lines.append("")

    if tech.home_base_city and tech.home_base_state:
        lines.append(f"Location: {tech.home_base_city}, {tech.home_base_state}")
    if tech.years_experience:
        lines.append(f"Experience: {tech.years_experience} years")
    lines.append(f"Career Stage: {tech.career_stage.value if tech.career_stage else 'N/A'}")
    lines.append(f"Status: {tech.deployability_status.value if tech.deployability_status else 'N/A'}")
    lines.append("")

    if data["skills"]:
        lines.append("SKILLS")
        lines.append("-" * 40)
        for s in data["skills"]:
            level = s.proficiency_level.value if s.proficiency_level else "Apprentice"
            lines.append(f"  • {s.skill_name} — {level}")
        lines.append("")

    if data["certifications"]:
        lines.append("CERTIFICATIONS")
        lines.append("-" * 40)
        for c in data["certifications"]:
            status = c.status.value if c.status else "Pending"
            expiry = c.expiry_date.strftime("%b %Y") if c.expiry_date else "N/A"
            lines.append(f"  • {c.cert_name} — {status} (Expires: {expiry})")
        lines.append("")

    if data["work_history"]:
        lines.append("WORK HISTORY")
        lines.append("-" * 40)
        for wh in data["work_history"]:
            start = wh["start_date"].strftime("%b %Y") if wh["start_date"] else "N/A"
            end = wh["end_date"].strftime("%b %Y") if wh["end_date"] else "Present"
            lines.append(f"  • {wh['project_name']} — {wh['role_name']}")
            lines.append(f"    {wh['location']} | {start} – {end}")
        lines.append("")

    if data["enrollments"]:
        lines.append("TRAINING PROGRAMS")
        lines.append("-" * 40)
        for e in data["enrollments"]:
            name = e.program.name if e.program else "Program"
            level = e.advancement_level.value if e.advancement_level else "Apprentice"
            hours = e.total_hours_logged or 0
            lines.append(f"  • {name} — {level} ({hours:.1f} hrs)")
        lines.append("")

    if data["badges"]:
        lines.append("BADGES & ACHIEVEMENTS")
        lines.append("-" * 40)
        for b in data["badges"]:
            lines.append(f"  • {b.badge_name} ({b.badge_type.value} badge)")

    text_content = "\n".join(lines)

    # Build a minimal valid PDF
    return _text_to_pdf(text_content)


def _text_to_pdf(text: str) -> bytes:
    """Convert plain text to a minimal valid PDF document."""
    # Escape special PDF characters
    escaped = (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )

    # Split into lines for PDF text rendering
    pdf_lines = escaped.split("\n")

    # Build PDF text stream with line positioning
    stream_lines = ["BT", "/F1 10 Tf", "1 0 0 1 50 750 Tm"]
    for line in pdf_lines:
        stream_lines.append(f"({line}) Tj")
        stream_lines.append("0 -14 Td")
    stream_lines.append("ET")
    stream_content = "\n".join(stream_lines)

    objects = []

    # Object 1: Catalog
    objects.append("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj")

    # Object 2: Pages
    objects.append("2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj")

    # Object 3: Page
    objects.append(
        "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj"
    )

    # Object 4: Content stream
    stream_bytes = stream_content.encode("latin-1", errors="replace")
    objects.append(
        f"4 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n"
        f"{stream_content}\nendstream\nendobj"
    )

    # Object 5: Font
    objects.append(
        "5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>\nendobj"
    )

    # Build PDF
    pdf_parts = ["%PDF-1.4\n"]
    offsets = []
    for obj in objects:
        offsets.append(len("".join(pdf_parts).encode("latin-1", errors="replace")))
        pdf_parts.append(obj + "\n")

    xref_offset = len("".join(pdf_parts).encode("latin-1", errors="replace"))
    pdf_parts.append("xref\n")
    pdf_parts.append(f"0 {len(objects) + 1}\n")
    pdf_parts.append("0000000000 65535 f \n")
    for offset in offsets:
        pdf_parts.append(f"{offset:010d} 00000 n \n")

    pdf_parts.append("trailer\n")
    pdf_parts.append(f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n")
    pdf_parts.append("startxref\n")
    pdf_parts.append(f"{xref_offset}\n")
    pdf_parts.append("%%EOF\n")

    return "".join(pdf_parts).encode("latin-1", errors="replace")
