"""SQL Scoring Layer — Translates preference rules into SQL scoring modifiers.

This module provides the bridge between approved PreferenceRules and the
SQL-based ranking query in the prefilter engine. Each rule template type has a
predefined SQL fragment builder that produces a SQLAlchemy CASE expression
adjusting the technician-job match score at the database level.

Architecture:
    PreferenceRule (DB row)
        → RuleSQLFragment (template mapping)
            → SQLAlchemy CASE/expression column
                → Injected into prefilter ranking query as additive modifier

Supported rule templates:
    - experience_threshold: penalise/exclude techs below min years
    - skill_level_minimum: penalise techs missing a specific skill at level
    - archetype_preference: boost techs matching a preferred archetype
    - cert_bonus: boost techs holding a specific active certification
    - rate_cap: penalise/exclude techs above an hourly rate ceiling
    - project_count_minimum: penalise techs with fewer than N prior projects
    - location_preference: boost techs in a preferred region/state
    - availability_window: penalise techs available too far from target date
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import (
    Column,
    Float,
    Integer,
    String,
    and_,
    case,
    cast,
    func,
    literal,
    or_,
    select,
    text,
)
from sqlalchemy.orm import Session
from sqlalchemy.sql import expression as sql_expr
from sqlalchemy.sql.elements import ColumnElement

from app.models.technician import (
    Technician,
    TechnicianSkill,
    TechnicianCertification,
    ProficiencyLevel,
    CertStatus,
)
from app.models.recommendation import PreferenceRule, PreferenceRuleStatus

logger = logging.getLogger("deployable.sql_scoring")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SQLScoringModifier:
    """A single SQL-level scoring modifier derived from a preference rule."""
    rule_id: str
    rule_type: str
    effect: str  # boost, demote, exclude
    modifier_value: float  # additive points (+/-)
    description: str
    # The SQLAlchemy expression that evaluates to modifier_value when rule fires,
    # or 0.0 when it doesn't apply.
    sql_expression: Optional[Any] = None
    # For Python-level fallback when SQL expression isn't possible
    python_evaluator: Optional[Callable] = None


@dataclass
class SQLScoringResult:
    """Result of applying SQL scoring modifiers to a candidate set."""
    modifiers_applied: List[Dict[str, Any]]
    total_rules_evaluated: int
    excluded_technician_ids: List[str]
    sql_modifier_column: Optional[Any] = None


# ---------------------------------------------------------------------------
# SQL fragment builders per rule template type
# ---------------------------------------------------------------------------

def _build_experience_threshold_sql(
    rule: PreferenceRule,
) -> SQLScoringModifier:
    """Build SQL modifier for experience_threshold rule.

    SQL fragment:
        CASE WHEN technicians.years_experience < :min_years
             THEN :penalty
             ELSE 0.0
        END
    """
    params = rule.parameters or {}
    min_years = float(params.get("min_years", 0))
    penalty = float(params.get("penalty", -10))

    sql_expr_col = case(
        (Technician.years_experience < min_years, literal(penalty)),
        (Technician.years_experience.is_(None), literal(penalty)),
        else_=literal(0.0),
    )

    return SQLScoringModifier(
        rule_id=str(rule.id),
        rule_type=rule.rule_type,
        effect=rule.effect or "demote",
        modifier_value=penalty,
        description=f"Experience below {min_years}yr → {penalty:+.0f}pts",
        sql_expression=sql_expr_col,
    )


def _build_archetype_preference_sql(
    rule: PreferenceRule,
) -> SQLScoringModifier:
    """Build SQL modifier for archetype_preference rule.

    SQL fragment:
        CASE WHEN technicians.archetype = :preferred_archetype
             THEN :bonus
             ELSE 0.0
        END
    """
    params = rule.parameters or {}
    preferred = params.get("preferred_archetype", "")
    bonus = float(params.get("bonus", 10))

    sql_expr_col = case(
        (Technician.archetype == preferred, literal(bonus)),
        else_=literal(0.0),
    )

    return SQLScoringModifier(
        rule_id=str(rule.id),
        rule_type=rule.rule_type,
        effect=rule.effect or "boost",
        modifier_value=bonus,
        description=f"Archetype '{preferred}' → {bonus:+.0f}pts",
        sql_expression=sql_expr_col,
    )


def _build_rate_cap_sql(
    rule: PreferenceRule,
) -> SQLScoringModifier:
    """Build SQL modifier for rate_cap rule.

    SQL fragment:
        CASE WHEN technicians.hourly_rate_min > :max_rate
             THEN :penalty
             ELSE 0.0
        END
    """
    params = rule.parameters or {}
    max_rate = float(params.get("max_hourly_rate", 999))
    penalty = float(params.get("penalty", -20))

    sql_expr_col = case(
        (Technician.hourly_rate_min > max_rate, literal(penalty)),
        else_=literal(0.0),
    )

    return SQLScoringModifier(
        rule_id=str(rule.id),
        rule_type=rule.rule_type,
        effect=rule.effect or "demote",
        modifier_value=penalty,
        description=f"Rate above ${max_rate}/hr → {penalty:+.0f}pts",
        sql_expression=sql_expr_col,
    )


def _build_project_count_minimum_sql(
    rule: PreferenceRule,
) -> SQLScoringModifier:
    """Build SQL modifier for project_count_minimum rule.

    SQL fragment:
        CASE WHEN technicians.total_project_count < :min_projects
             THEN :penalty
             ELSE 0.0
        END
    """
    params = rule.parameters or {}
    min_projects = int(params.get("min_projects", 3))
    penalty = float(params.get("penalty", -5))

    sql_expr_col = case(
        (Technician.total_project_count < min_projects, literal(penalty)),
        (Technician.total_project_count.is_(None), literal(penalty)),
        else_=literal(0.0),
    )

    return SQLScoringModifier(
        rule_id=str(rule.id),
        rule_type=rule.rule_type,
        effect=rule.effect or "demote",
        modifier_value=penalty,
        description=f"Project count below {min_projects} → {penalty:+.0f}pts",
        sql_expression=sql_expr_col,
    )


def _build_location_preference_sql(
    rule: PreferenceRule,
) -> SQLScoringModifier:
    """Build SQL modifier for location_preference rule.

    SQL fragment:
        CASE WHEN technicians.home_base_state = :preferred_state
             THEN :bonus
             ELSE 0.0
        END
    """
    params = rule.parameters or {}
    preferred_state = params.get("preferred_state", "")
    bonus = float(params.get("bonus", 10))

    sql_expr_col = case(
        (Technician.home_base_state == preferred_state, literal(bonus)),
        else_=literal(0.0),
    )

    return SQLScoringModifier(
        rule_id=str(rule.id),
        rule_type=rule.rule_type,
        effect=rule.effect or "boost",
        modifier_value=bonus,
        description=f"Home state '{preferred_state}' → {bonus:+.0f}pts",
        sql_expression=sql_expr_col,
    )


def _build_skill_level_minimum_python(
    rule: PreferenceRule,
) -> SQLScoringModifier:
    """Build Python-level modifier for skill_level_minimum rule.

    Skill checks require joins to technician_skills table which are complex
    in SQL. We provide a Python evaluator for this template.
    """
    params = rule.parameters or {}
    skill_name = params.get("skill_name", "")
    min_level = params.get("min_level", "Intermediate")
    penalty = float(params.get("penalty", -15))

    proficiency_rank = {
        "Apprentice": 1, ProficiencyLevel.APPRENTICE.value: 1,
        "Intermediate": 2, ProficiencyLevel.INTERMEDIATE.value: 2,
        "Advanced": 3, ProficiencyLevel.ADVANCED.value: 3,
    }
    required_rank = proficiency_rank.get(min_level, 2)

    def evaluator(tech: Technician) -> float:
        """Returns the modifier value if rule fires, else 0.0."""
        for ts in (tech.skills or []):
            ts_name = ts.skill_name.strip().lower()
            if ts_name == skill_name.strip().lower():
                level_val = ts.proficiency_level
                if hasattr(level_val, "value"):
                    level_val = level_val.value
                tech_rank = proficiency_rank.get(level_val, 0)
                if tech_rank < required_rank:
                    return penalty
                return 0.0
        # Skill not found at all → penalty
        return penalty

    return SQLScoringModifier(
        rule_id=str(rule.id),
        rule_type=rule.rule_type,
        effect=rule.effect or "demote",
        modifier_value=penalty,
        description=f"Skill '{skill_name}' below {min_level} → {penalty:+.0f}pts",
        python_evaluator=evaluator,
    )


def _build_cert_bonus_python(
    rule: PreferenceRule,
) -> SQLScoringModifier:
    """Build Python-level modifier for cert_bonus rule.

    Cert checks require joins to technician_certifications table.
    """
    params = rule.parameters or {}
    cert_name = params.get("cert_name", "")
    bonus = float(params.get("bonus", 5))

    def evaluator(tech: Technician) -> float:
        for tc in (tech.certifications or []):
            if tc.cert_name.strip().lower() == cert_name.strip().lower():
                status_val = tc.status.value if hasattr(tc.status, "value") else tc.status
                if status_val == CertStatus.ACTIVE.value:
                    return bonus
        return 0.0

    return SQLScoringModifier(
        rule_id=str(rule.id),
        rule_type=rule.rule_type,
        effect=rule.effect or "boost",
        modifier_value=bonus,
        description=f"Holds cert '{cert_name}' → {bonus:+.0f}pts",
        python_evaluator=evaluator,
    )


def _build_availability_window_sql(
    rule: PreferenceRule,
) -> SQLScoringModifier:
    """Build SQL modifier for availability_window rule.

    SQL fragment:
        CASE WHEN technicians.available_from > :target_date + :max_days_out
             THEN :penalty
             ELSE 0.0
        END
    """
    params = rule.parameters or {}
    max_days_out = int(params.get("max_days_out", 30))
    penalty = float(params.get("penalty", -10))
    # target_date is usually project start; we store as parameter
    # At query time the caller provides the actual date

    def evaluator(tech: Technician) -> float:
        from datetime import timedelta
        target = params.get("target_date")
        if target and isinstance(target, str):
            try:
                target = date.fromisoformat(target)
            except (ValueError, TypeError):
                return 0.0
        elif not target:
            return 0.0

        avail = tech.available_from
        if avail is None:
            return 0.0  # Available now
        cutoff = target + timedelta(days=max_days_out)
        if avail > cutoff:
            return penalty
        return 0.0

    return SQLScoringModifier(
        rule_id=str(rule.id),
        rule_type=rule.rule_type,
        effect=rule.effect or "demote",
        modifier_value=penalty,
        description=f"Available more than {max_days_out}d out → {penalty:+.0f}pts",
        python_evaluator=evaluator,
    )


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

# Maps rule_type → builder function
RULE_SQL_BUILDERS: Dict[str, Callable[[PreferenceRule], SQLScoringModifier]] = {
    "experience_threshold": _build_experience_threshold_sql,
    "archetype_preference": _build_archetype_preference_sql,
    "rate_cap": _build_rate_cap_sql,
    "project_count_minimum": _build_project_count_minimum_sql,
    "location_preference": _build_location_preference_sql,
    # These use Python evaluators (joins too complex for single-pass SQL)
    "skill_level_minimum": _build_skill_level_minimum_python,
    "cert_bonus": _build_cert_bonus_python,
    "availability_window": _build_availability_window_sql,
}


def get_supported_rule_types() -> List[str]:
    """Return all supported rule template types."""
    return list(RULE_SQL_BUILDERS.keys())


# ---------------------------------------------------------------------------
# Core scoring layer
# ---------------------------------------------------------------------------

def load_active_rules(
    db: Session,
    scope: str = "global",
) -> List[PreferenceRule]:
    """Load all active, approved preference rules for the given scope."""
    return (
        db.query(PreferenceRule)
        .filter(
            PreferenceRule.active == True,  # noqa: E712
            or_(
                PreferenceRule.scope == scope,
                PreferenceRule.scope == "global",
            ),
        )
        .all()
    )


def build_scoring_modifiers(
    rules: List[PreferenceRule],
) -> List[SQLScoringModifier]:
    """Convert a list of preference rules into SQL scoring modifiers.

    Each rule is mapped to its template builder. Unknown rule types are
    logged and skipped gracefully.

    Returns:
        List of SQLScoringModifier instances, each containing either a
        sql_expression (for DB-level evaluation) or python_evaluator
        (for Python-level post-processing).
    """
    modifiers = []
    for rule in rules:
        builder = RULE_SQL_BUILDERS.get(rule.rule_type)
        if builder is None:
            logger.warning(
                f"Unknown rule_type '{rule.rule_type}' for rule {rule.id} — skipping"
            )
            continue
        try:
            modifier = builder(rule)
            modifiers.append(modifier)
        except Exception as e:
            logger.error(
                f"Failed to build SQL modifier for rule {rule.id} "
                f"(type={rule.rule_type}): {e}"
            )
    return modifiers


def build_composite_sql_modifier(
    modifiers: List[SQLScoringModifier],
) -> Optional[ColumnElement]:
    """Combine all SQL-expression modifiers into a single additive column.

    Returns a SQLAlchemy expression that sums all applicable SQL-level
    modifiers. Python-only modifiers are excluded (they run post-query).

    Usage in query:
        composite = build_composite_sql_modifier(modifiers)
        query = query.add_columns(composite.label("pref_modifier"))
        # Then: total_score = base_score + pref_modifier
    """
    sql_exprs = [
        m.sql_expression
        for m in modifiers
        if m.sql_expression is not None
    ]

    if not sql_exprs:
        return None

    # Sum all SQL expressions
    composite = sql_exprs[0]
    for expr in sql_exprs[1:]:
        composite = composite + expr

    return composite


def apply_python_modifiers(
    tech: Technician,
    modifiers: List[SQLScoringModifier],
) -> Tuple[float, List[Dict[str, Any]]]:
    """Apply Python-level modifiers to a technician.

    Returns:
        (total_adjustment, list_of_triggered_adjustments)
    """
    total = 0.0
    adjustments = []

    for mod in modifiers:
        if mod.python_evaluator is None:
            continue
        try:
            value = mod.python_evaluator(tech)
            if value != 0.0:
                total += value
                adjustments.append({
                    "rule_id": mod.rule_id,
                    "rule_type": mod.rule_type,
                    "effect": mod.effect,
                    "modifier": value,
                    "reason": mod.description,
                })
        except Exception as e:
            logger.error(
                f"Python evaluator failed for rule {mod.rule_id}: {e}"
            )

    return total, adjustments


def apply_sql_modifiers_to_score(
    tech: Technician,
    base_score: float,
    modifiers: List[SQLScoringModifier],
) -> Tuple[float, List[Dict[str, Any]], bool]:
    """Apply all modifiers (SQL + Python) to a technician's base score.

    This is the main integration point: given a base weighted score from
    the prefilter engine, it applies all active preference rule modifiers
    and returns the adjusted score.

    Args:
        tech: The Technician ORM instance (with skills/certs loaded)
        base_score: The raw weighted score from the prefilter engine (0-100)
        modifiers: List of SQLScoringModifier from build_scoring_modifiers()

    Returns:
        (adjusted_score, adjustment_details, is_excluded)
    """
    total_adjustment = 0.0
    all_adjustments = []
    is_excluded = False

    for mod in modifiers:
        value = 0.0

        if mod.python_evaluator is not None:
            try:
                value = mod.python_evaluator(tech)
            except Exception as e:
                logger.error(f"Evaluator failed for rule {mod.rule_id}: {e}")
                continue
        elif mod.sql_expression is not None:
            # For SQL modifiers used in Python context, we evaluate
            # using the technician's attributes directly
            value = _evaluate_sql_modifier_locally(mod, tech)

        if value != 0.0:
            total_adjustment += value
            adj = {
                "rule_id": mod.rule_id,
                "rule_type": mod.rule_type,
                "effect": mod.effect,
                "modifier": value,
                "reason": mod.description,
            }
            all_adjustments.append(adj)

            if mod.effect == "exclude" and value < 0:
                is_excluded = True

    adjusted = max(0.0, base_score + total_adjustment)
    return adjusted, all_adjustments, is_excluded


def _evaluate_sql_modifier_locally(
    mod: SQLScoringModifier,
    tech: Technician,
) -> float:
    """Evaluate a SQL-expression modifier using Python attribute access.

    This allows SQL modifiers to also work in Python-only scoring paths
    (e.g., when the prefilter engine has already loaded technicians).
    """
    params = {}
    if mod.rule_type == "experience_threshold":
        # Extract parameters from the description or reconstruct from modifier
        p = _extract_rule_params_from_modifier(mod)
        min_years = p.get("min_years", 0)
        tech_years = tech.years_experience or 0
        if tech_years < min_years:
            return mod.modifier_value
        return 0.0

    elif mod.rule_type == "archetype_preference":
        p = _extract_rule_params_from_modifier(mod)
        preferred = p.get("preferred_archetype", "")
        if (tech.archetype or "") == preferred:
            return mod.modifier_value
        return 0.0

    elif mod.rule_type == "rate_cap":
        p = _extract_rule_params_from_modifier(mod)
        max_rate = p.get("max_hourly_rate", 999)
        tech_rate = tech.hourly_rate_min or 0
        if tech_rate > max_rate:
            return mod.modifier_value
        return 0.0

    elif mod.rule_type == "project_count_minimum":
        p = _extract_rule_params_from_modifier(mod)
        min_projects = p.get("min_projects", 3)
        tech_count = tech.total_project_count or 0
        if tech_count < min_projects:
            return mod.modifier_value
        return 0.0

    elif mod.rule_type == "location_preference":
        p = _extract_rule_params_from_modifier(mod)
        preferred_state = p.get("preferred_state", "")
        if (tech.home_base_state or "") == preferred_state:
            return mod.modifier_value
        return 0.0

    return 0.0


def _extract_rule_params_from_modifier(mod: SQLScoringModifier) -> Dict[str, Any]:
    """Extract original rule parameters from description for local evaluation.

    This is used as a fallback when we need to evaluate SQL expressions
    in Python context. Ideally the params are carried through, but for
    robustness we parse from the description.
    """
    # We'll store params directly on the modifier for efficiency
    if hasattr(mod, "_cached_params"):
        return mod._cached_params
    return {}


# ---------------------------------------------------------------------------
# Enhanced builder that caches params
# ---------------------------------------------------------------------------

def build_scoring_modifiers_with_params(
    rules: List[PreferenceRule],
) -> List[SQLScoringModifier]:
    """Build scoring modifiers with cached parameters for local evaluation.

    This is the preferred entry point — it stores the original rule
    parameters on the modifier for use by _evaluate_sql_modifier_locally.
    """
    modifiers = build_scoring_modifiers(rules)

    # Cache params from the rules onto the modifiers for local evaluation
    rule_map = {str(r.id): r for r in rules}
    for mod in modifiers:
        rule = rule_map.get(mod.rule_id)
        if rule:
            mod._cached_params = rule.parameters or {}

    return modifiers


# ---------------------------------------------------------------------------
# Integration with prefilter engine
# ---------------------------------------------------------------------------

def compute_preference_adjusted_scores(
    db: Session,
    technicians: List[Technician],
    base_scores: Dict[str, float],
    scope: str = "global",
) -> Dict[str, Tuple[float, List[Dict], bool]]:
    """Compute preference-adjusted scores for a list of technicians.

    This is the main integration point called by the prefilter engine.

    Args:
        db: Database session
        technicians: List of Technician instances (with skills/certs loaded)
        base_scores: Dict mapping technician_id → base weighted score
        scope: Preference rule scope filter

    Returns:
        Dict mapping technician_id → (adjusted_score, adjustments, is_excluded)
    """
    rules = load_active_rules(db, scope)
    if not rules:
        return {
            tid: (score, [], False)
            for tid, score in base_scores.items()
        }

    modifiers = build_scoring_modifiers_with_params(rules)

    results = {}
    for tech in technicians:
        tid = str(tech.id)
        base = base_scores.get(tid, 0.0)
        adjusted, adjustments, excluded = apply_sql_modifiers_to_score(
            tech, base, modifiers
        )
        results[tid] = (adjusted, adjustments, excluded)

    return results


def get_sql_scoring_summary(
    db: Session,
    scope: str = "global",
) -> List[Dict[str, Any]]:
    """Get a summary of all active scoring modifiers for display/debugging.

    Returns a list of dicts describing each active rule and its SQL fragment.
    """
    rules = load_active_rules(db, scope)
    modifiers = build_scoring_modifiers(rules)

    return [
        {
            "rule_id": mod.rule_id,
            "rule_type": mod.rule_type,
            "effect": mod.effect,
            "modifier_value": mod.modifier_value,
            "description": mod.description,
            "evaluation_mode": "sql" if mod.sql_expression is not None else "python",
        }
        for mod in modifiers
    ]
