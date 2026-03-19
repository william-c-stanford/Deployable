"""SQLAlchemy models package — single source of truth for all tables."""

# Technician domain
from app.models.technician import (  # noqa: F401
    Technician,
    TechnicianSkill,
    TechnicianCertification,
    TechnicianDocument,
    TechnicianBadge,
    CareerStage,
    DeployabilityStatus,
    ProficiencyLevel,
    CertStatus,
    VerificationStatus,
    BadgeType,
    TransitionalTrigger,
    TransitionalResolutionType,
)

# Transitional state tracking (auto-resolving statuses)
from app.models.transitional_state import TransitionalState  # noqa: F401

# Skills taxonomy
from app.models.skill import Skill, SkillCategory  # noqa: F401

# Certification definitions
from app.models.certification import Certification  # noqa: F401

# Users & partners
from app.models.user import User, Partner, UserRole  # noqa: F401

# Projects & roles
from app.models.project import Project, ProjectRole, ProjectStatus  # noqa: F401

# Assignments
from app.models.assignment import Assignment, AssignmentType, AssignmentStatus, ChainPriority  # noqa: F401

# Assignment confirmations (partner flow) with escalation tracking
from app.models.assignment_confirmation import (  # noqa: F401
    AssignmentConfirmation,
    ConfirmationStatus,
    ConfirmationType,
    EscalationStatus,
)

# Timesheets
from app.models.timesheet import Timesheet, TimesheetStatus  # noqa: F401

# Recommendations & preference rules
from app.models.recommendation import (  # noqa: F401
    Recommendation,
    RecommendationStatus,
    RecommendationType,
    PreferenceRule,
    PreferenceRuleTemplateType,
    PreferenceRuleStatus,
    PreferenceRuleCreatedByType,
)

# Audit, actions, headcount
from app.models.audit import (  # noqa: F401
    AuditLog,
    SuggestedAction,
    PendingHeadcountRequest,
    HeadcountRequestStatus,
)

# Chat
from app.models.chat import ChatSession, ChatMessage  # noqa: F401

# Training programs, enrollment, hours tracking, and advancement gates
from app.models.training import (  # noqa: F401
    TrainingProgram,
    TrainingEnrollment,
    TrainingHoursLog,
    AdvancementGateConfig,
    AdvancementLevel,
    EnrollmentStatus,
    HoursLogSource,
)

# Partner notifications (48-hour advance visibility)
from app.models.partner_notification import (  # noqa: F401
    PartnerNotification,
    NotificationType,
    NotificationStatus,
)

# Event log (worker audit trail)
from app.models.event_log import EventLog  # noqa: F401

# Career Passport shareable tokens
from app.models.career_passport_token import CareerPassportToken  # noqa: F401

# Badge models (manual site/client badges and auto-generated milestone badges)
from app.models.badge import (  # noqa: F401
    ManualBadge,
    MilestoneBadge,
    ManualBadgeCategory,
    MilestoneType,
)

# Recommendation merge history and batch job execution audit logs
from app.models.merge_history import (  # noqa: F401
    RecommendationMergeHistory,
    BatchJobExecution,
    MergeAction,
    BatchJobStatus,
    BatchJobType,
)

# Deployability status history
from app.models.deployability_history import (  # noqa: F401
    DeployabilityStatusHistory,
    StatusChangeSource,
)

# Skill breakdowns (assignment completion reviews with partner review)
from app.models.skill_breakdown import (  # noqa: F401
    SkillBreakdown,
    SkillBreakdownItem,
    SkillProficiencyRating,
    PartnerReviewStatus,
)
