"""Career Passport endpoints — token management, PDF export, and shareable pages.

Provides:
- Token generation, listing, revocation, and validation for shareable links
- PDF export of career passport (authenticated for ops/technician, public via token)
- Server-rendered HTML passport page (public via token)
- JSON API for career passport data (public via token)
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.orm import Session, joinedload

from app.auth import CurrentUser, get_current_user, require_role
from app.database import get_db
from app.models.career_passport_token import CareerPassportToken, DEFAULT_TOKEN_EXPIRY_DAYS
from app.models.technician import Technician, TechnicianSkill, TechnicianCertification, TechnicianBadge
from app.models.training import TrainingEnrollment, TrainingProgram, EnrollmentStatus
from app.schemas.career_passport import (
    CareerPassportTokenCreate,
    CareerPassportTokenListResponse,
    CareerPassportTokenResponse,
    CareerPassportTokenRevokeResponse,
    PublicCareerPassportView,
    PublicSkillView,
    PublicCertView,
    PublicBadgeView,
    PublicEnrollmentView,
)
from app.services.career_passport import (
    generate_passport_pdf,
    render_passport_html,
)

logger = logging.getLogger(__name__)

# Jinja2 template setup
from jinja2 import Environment, FileSystemLoader

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=True,
)

router = APIRouter(prefix="/api/career-passport", tags=["career-passport"])


def _build_share_url(token: str) -> str:
    """Construct the shareable URL for a career passport token."""
    return f"/passport/{token}"


def _token_to_response(t: CareerPassportToken) -> CareerPassportTokenResponse:
    return CareerPassportTokenResponse(
        id=t.id,
        technician_id=t.technician_id,
        token=t.token,
        label=t.label,
        revoked=t.revoked,
        expires_at=t.expires_at,
        created_at=t.created_at,
        created_by_role=t.created_by_role,
        is_active=t.is_active,
        share_url=_build_share_url(t.token),
    )


def _check_technician_access(
    technician_id: UUID, current_user: CurrentUser, db: Session
) -> Technician:
    """Verify the technician exists and the current user has access.

    - ops: can access any technician
    - technician: can only access their own passport
    - partner: no access to generate/revoke tokens
    """
    technician = db.query(Technician).filter(Technician.id == technician_id).first()
    if not technician:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Technician {technician_id} not found",
        )

    if current_user.role == "technician":
        # Technician can only manage their own passport tokens
        if current_user.user_id != str(technician_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Technicians can only manage their own career passport tokens",
            )

    return technician


# ---------------------------------------------------------------------------
# Generate a new shareable token
# ---------------------------------------------------------------------------

@router.post(
    "/tokens",
    response_model=CareerPassportTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a shareable career passport token",
)
def generate_token(
    body: CareerPassportTokenCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops", "technician")),
):
    """Generate a new shareable career passport token with 30-day default expiry.

    - **ops** users can generate tokens for any technician.
    - **technician** users can only generate tokens for themselves.
    - **partner** users are not permitted.
    """
    _check_technician_access(body.technician_id, current_user, db)

    expiry_days = body.expiry_days or DEFAULT_TOKEN_EXPIRY_DAYS
    token = CareerPassportToken(
        technician_id=body.technician_id,
        label=body.label,
        created_by_user_id=current_user.user_id,
        created_by_role=current_user.role,
        expires_at=datetime.utcnow() + timedelta(days=expiry_days),
    )
    db.add(token)
    db.commit()
    db.refresh(token)

    return _token_to_response(token)


# ---------------------------------------------------------------------------
# List tokens for a technician
# ---------------------------------------------------------------------------

@router.get(
    "/tokens/technician/{technician_id}",
    response_model=CareerPassportTokenListResponse,
    summary="List career passport tokens for a technician",
)
def list_tokens(
    technician_id: UUID,
    include_revoked: bool = False,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops", "technician")),
):
    """List all (or active-only) career passport tokens for a technician.

    - **ops** users can list tokens for any technician.
    - **technician** users can only list their own tokens.
    """
    _check_technician_access(technician_id, current_user, db)

    query = db.query(CareerPassportToken).filter(
        CareerPassportToken.technician_id == technician_id
    )
    if not include_revoked:
        query = query.filter(CareerPassportToken.revoked == False)  # noqa: E712

    tokens = query.order_by(CareerPassportToken.created_at.desc()).all()
    return CareerPassportTokenListResponse(
        tokens=[_token_to_response(t) for t in tokens],
        count=len(tokens),
    )


# ---------------------------------------------------------------------------
# Revoke a token
# ---------------------------------------------------------------------------

@router.post(
    "/tokens/{token_id}/revoke",
    response_model=CareerPassportTokenRevokeResponse,
    summary="Revoke a career passport token",
)
def revoke_token(
    token_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops", "technician")),
):
    """Revoke a shareable career passport token so its link no longer works.

    - **ops** users can revoke any token.
    - **technician** users can only revoke their own tokens.
    """
    token = (
        db.query(CareerPassportToken)
        .filter(CareerPassportToken.id == token_id)
        .first()
    )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Token {token_id} not found",
        )

    # Permission check: technicians can only revoke their own
    _check_technician_access(token.technician_id, current_user, db)

    if token.revoked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Token is already revoked",
        )

    token.revoked = True
    token.revoked_at = datetime.utcnow()
    db.commit()
    db.refresh(token)

    return CareerPassportTokenRevokeResponse(
        id=token.id,
        token=token.token,
        revoked=True,
        revoked_at=token.revoked_at,
    )


# ---------------------------------------------------------------------------
# Validate a token (public — no auth required)
# ---------------------------------------------------------------------------

@router.get(
    "/validate/{token_value}",
    summary="Validate a career passport share token (public)",
)
def validate_token(
    token_value: str,
    db: Session = Depends(get_db),
):
    """Public endpoint to validate a career passport token.

    Returns the technician_id if the token is valid and active.
    Used by the shareable passport page renderer.
    """
    token = (
        db.query(CareerPassportToken)
        .filter(CareerPassportToken.token == token_value)
        .first()
    )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or unknown token",
        )

    if token.revoked:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This career passport link has been revoked",
        )

    if token.is_expired:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This career passport link has expired",
        )

    return {
        "valid": True,
        "technician_id": str(token.technician_id),
        "expires_at": token.expires_at.isoformat(),
        "label": token.label,
    }


# ---------------------------------------------------------------------------
# Helpers for public view
# ---------------------------------------------------------------------------

def _validate_share_token(token_value: str, db: Session) -> CareerPassportToken:
    """Look up and validate a share token, returning the model or raising."""
    token_record = (
        db.query(CareerPassportToken)
        .filter(CareerPassportToken.token == token_value)
        .first()
    )
    if not token_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or unknown token",
        )
    if token_record.revoked:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This career passport link has been revoked",
        )
    if token_record.is_expired:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This career passport link has expired",
        )
    return token_record


def _load_technician_full(db: Session, technician_id) -> Technician:
    """Load technician with all nested relationships for passport display."""
    technician = (
        db.query(Technician)
        .options(
            joinedload(Technician.skills),
            joinedload(Technician.certifications),
            joinedload(Technician.badges),
            joinedload(Technician.training_enrollments).joinedload(TrainingEnrollment.program),
        )
        .filter(Technician.id == technician_id)
        .first()
    )
    if not technician:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Technician not found",
        )
    return technician


def _render_error_html(icon: str, title: str, message: str, status_code: int = 404) -> HTMLResponse:
    """Render a branded error page."""
    template = _jinja_env.get_template("career_passport_error.html")
    html = template.render(icon=icon, title=title, message=message)
    return HTMLResponse(content=html, status_code=status_code)


# ---------------------------------------------------------------------------
# Public shareable passport — HTML (Jinja2 server-rendered)
# ---------------------------------------------------------------------------

@router.get(
    "/public/{token_value}",
    response_class=HTMLResponse,
    summary="Public career passport page (HTML, unauthenticated)",
    include_in_schema=True,
)
def public_passport_html(
    token_value: str,
    db: Session = Depends(get_db),
):
    """Unauthenticated public endpoint that validates the expiring share token
    and renders a read-only career passport as a server-rendered HTML page.

    This is the shareable URL that ops users or technicians can send to
    partners, recruiters, or clients. No login required.

    Returns:
        HTML page with technician's skills, certifications, badges,
        training progress, and career summary.
    """
    # Validate token
    token_record = (
        db.query(CareerPassportToken)
        .filter(CareerPassportToken.token == token_value)
        .first()
    )

    if not token_record:
        return _render_error_html(
            icon="\U0001F50D",
            title="Link Not Found",
            message="This career passport link is invalid or does not exist. Please check the URL and try again.",
            status_code=404,
        )

    if token_record.revoked:
        return _render_error_html(
            icon="\U0001F6AB",
            title="Link Revoked",
            message="This career passport link has been revoked by the owner and is no longer accessible.",
            status_code=410,
        )

    if token_record.is_expired:
        return _render_error_html(
            icon="\u23F0",
            title="Link Expired",
            message=f"This career passport link expired on {token_record.expires_at.strftime('%B %d, %Y')}. Please request a new link.",
            status_code=410,
        )

    # Load full technician data
    technician = _load_technician_full(db, token_record.technician_id)

    # Prepare template context
    skills = sorted(technician.skills, key=lambda s: s.skill_name) if technician.skills else []
    certifications = sorted(
        technician.certifications, key=lambda c: c.cert_name
    ) if technician.certifications else []
    badges = technician.badges or []

    active_cert_count = sum(
        1 for c in certifications
        if hasattr(c.status, 'value') and c.status.value == 'Active'
        or (isinstance(c.status, str) and c.status == 'Active')
    )

    # Filter to active training enrollments
    enrollments = [
        e for e in (technician.training_enrollments or [])
        if hasattr(e.status, 'value') and e.status.value in ('Active', 'Completed')
        or (isinstance(e.status, str) and e.status in ('Active', 'Completed'))
    ]

    template = _jinja_env.get_template("career_passport.html")
    html = template.render(
        technician=technician,
        skills=skills,
        certifications=certifications,
        badges=badges,
        active_cert_count=active_cert_count,
        enrollments=enrollments,
        expires_at=token_record.expires_at,
        token=token_value,
    )

    return HTMLResponse(content=html, status_code=200)


# ---------------------------------------------------------------------------
# Public shareable passport — JSON API
# ---------------------------------------------------------------------------

@router.get(
    "/public/{token_value}/json",
    response_model=PublicCareerPassportView,
    summary="Public career passport data (JSON, unauthenticated)",
    include_in_schema=True,
)
def public_passport_json(
    token_value: str,
    db: Session = Depends(get_db),
):
    """Unauthenticated JSON API endpoint for career passport data.

    Same validation as the HTML endpoint but returns structured JSON.
    Useful for partner integrations or custom renderers.
    """
    token_record = _validate_share_token(token_value, db)
    technician = _load_technician_full(db, token_record.technician_id)

    skills = [
        PublicSkillView(
            skill_name=s.skill_name,
            proficiency_level=s.proficiency_level.value if hasattr(s.proficiency_level, 'value') else str(s.proficiency_level),
        )
        for s in (technician.skills or [])
    ]

    certifications = [
        PublicCertView(
            cert_name=c.cert_name,
            status=c.status.value if hasattr(c.status, 'value') else str(c.status),
            expiry_date=c.expiry_date,
        )
        for c in (technician.certifications or [])
    ]

    badges = [
        PublicBadgeView(
            badge_name=b.badge_name,
            badge_type=b.badge_type.value if hasattr(b.badge_type, 'value') else str(b.badge_type),
            description=b.description,
        )
        for b in (technician.badges or [])
    ]

    enrollments = [
        PublicEnrollmentView(
            program_name=e.program.name if e.program else "Unknown Program",
            advancement_level=e.advancement_level.value if hasattr(e.advancement_level, 'value') else str(e.advancement_level),
            total_hours_logged=e.total_hours_logged or 0,
            total_hours_required=e.program.total_hours_required if e.program else 0,
            status=e.status.value if hasattr(e.status, 'value') else str(e.status),
        )
        for e in (technician.training_enrollments or [])
        if hasattr(e.status, 'value') and e.status.value in ('Active', 'Completed')
        or (isinstance(e.status, str) and e.status in ('Active', 'Completed'))
    ]

    active_cert_count = sum(
        1 for c in certifications if c.status == 'Active'
    )

    cs = technician.career_stage
    ds = technician.deployability_status

    return PublicCareerPassportView(
        first_name=technician.first_name,
        last_name=technician.last_name,
        home_base_city=technician.home_base_city,
        home_base_state=technician.home_base_state,
        years_experience=technician.years_experience or 0,
        career_stage=cs.value if hasattr(cs, 'value') else str(cs),
        deployability_status=ds.value if hasattr(ds, 'value') else str(ds),
        archetype=technician.archetype,
        total_project_count=technician.total_project_count or 0,
        total_approved_hours=technician.total_approved_hours or 0,
        docs_verified=technician.docs_verified or False,
        bio=technician.bio,
        skills=skills,
        certifications=certifications,
        badges=badges,
        training_enrollments=enrollments,
        active_cert_count=active_cert_count,
        token_expires_at=token_record.expires_at,
    )


# ---------------------------------------------------------------------------
# PDF Export — authenticated (ops / technician)
# ---------------------------------------------------------------------------

@router.get(
    "/pdf/{technician_id}",
    summary="Download career passport as PDF (authenticated)",
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Career passport PDF document",
        }
    },
)
def download_passport_pdf(
    technician_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops", "technician")),
):
    """Generate and download a career passport PDF for a technician.

    Compiles certifications, work history, skills, training enrollments,
    and badges into a formatted PDF document using WeasyPrint.

    - **ops** users can download any technician's passport.
    - **technician** users can only download their own passport.
    - **partner** users are not permitted (use the public token-based endpoint).
    """
    _check_technician_access(technician_id, current_user, db)

    result = generate_passport_pdf(db, technician_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Technician {technician_id} not found",
        )

    pdf_bytes, filename = result
    logger.info(
        "Generated career passport PDF for technician %s (%d bytes)",
        technician_id,
        len(pdf_bytes),
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


# ---------------------------------------------------------------------------
# PDF Export — public via share token (no auth required)
# ---------------------------------------------------------------------------

@router.get(
    "/public/{token_value}/pdf",
    summary="Download career passport PDF via share token (public)",
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Career passport PDF document",
        }
    },
)
def public_passport_pdf(
    token_value: str,
    db: Session = Depends(get_db),
):
    """Public (unauthenticated) endpoint to download a career passport PDF
    using a valid share token.

    The token must be active (not revoked or expired). This allows partners,
    recruiters, or clients to download a PDF copy of the passport from the
    shareable link.
    """
    token_record = _validate_share_token(token_value, db)

    result = generate_passport_pdf(db, token_record.technician_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Technician data not found",
        )

    pdf_bytes, filename = result
    logger.info(
        "Generated public career passport PDF via token %s... (%d bytes)",
        token_value[:8],
        len(pdf_bytes),
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )
