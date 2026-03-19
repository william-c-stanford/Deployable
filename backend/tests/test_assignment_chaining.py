"""Tests for the 90-day assignment chaining engine.

Covers:
  • Gap detection between assignments
  • Overlap detection and resolution proposals
  • Chain continuity validation
  • Implicit chain detection
  • Rolling-off-soon identification
  • Utility functions (gap classification, chain link conversion)
"""

import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.services.assignment_chaining import (
    # Constants
    ROLLING_WINDOW_DAYS,
    MAX_GAP_SEAMLESS_DAYS,
    MAX_GAP_ACCEPTABLE_DAYS,
    MAX_GAP_WARNING_DAYS,
    # Enums
    GapSeverity,
    OverlapType,
    ContinuityIssueType,
    # Functions
    _classify_gap,
    detect_gaps,
    detect_overlaps,
    propose_overlap_resolution,
    validate_chain_continuity,
    build_implicit_chain,
    calculate_rolling_off_soon,
    build_chain,
    build_technician_timeline,
    build_forward_schedule,
)
from app.models.assignment import AssignmentType, AssignmentStatus


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


def make_assignment(
    start_date: date,
    end_date: date = None,
    status: str = "Active",
    assignment_type: str = "Active",
    technician_id: str = None,
    chain_id: str = None,
    chain_position: int = None,
    chain_priority: str = None,
    previous_assignment_id: str = None,
    next_assignment_id: str = None,
    is_forward_booked: bool = False,
    booking_confidence: float = None,
    confirmed_at=None,
    chain_notes: str = None,
    role_name: str = "Splicer",
    project_name: str = "Metro Fiber Build",
):
    """Create a mock Assignment ORM object for testing."""
    a = MagicMock()
    a.id = uuid.uuid4()
    a.technician_id = technician_id or uuid.uuid4()
    a.role_id = uuid.uuid4()
    a.start_date = start_date
    a.end_date = end_date
    a.status = status
    a.assignment_type = assignment_type
    a.chain_id = chain_id
    a.chain_position = chain_position
    a.chain_priority = chain_priority
    a.previous_assignment_id = previous_assignment_id
    a.next_assignment_id = next_assignment_id
    a.is_forward_booked = is_forward_booked
    a.booking_confidence = booking_confidence
    a.confirmed_at = confirmed_at
    a.chain_notes = chain_notes
    a.hourly_rate = 55.0
    a.per_diem = 120.0
    a.partner_confirmed_start = False
    a.partner_confirmed_end = False

    # Mock relationships
    tech = MagicMock()
    tech.full_name = "Marcus Rivera"
    a.technician = tech

    role = MagicMock()
    role.role_name = role_name
    project = MagicMock()
    project.id = uuid.uuid4()
    project.name = project_name
    role.project = project
    a.role = role

    return a


# ---------------------------------------------------------------------------
# Gap Classification
# ---------------------------------------------------------------------------

class TestGapClassification:
    def test_seamless_gap(self):
        assert _classify_gap(0) == GapSeverity.SEAMLESS

    def test_acceptable_gap(self):
        assert _classify_gap(1) == GapSeverity.ACCEPTABLE
        assert _classify_gap(7) == GapSeverity.ACCEPTABLE

    def test_warning_gap(self):
        assert _classify_gap(8) == GapSeverity.WARNING
        assert _classify_gap(14) == GapSeverity.WARNING

    def test_critical_gap(self):
        assert _classify_gap(15) == GapSeverity.CRITICAL
        assert _classify_gap(30) == GapSeverity.CRITICAL

    def test_negative_gap_treated_as_seamless(self):
        # Negative would mean overlap - but classify_gap handles non-negative
        assert _classify_gap(-1) == GapSeverity.SEAMLESS


# ---------------------------------------------------------------------------
# Gap Detection
# ---------------------------------------------------------------------------

class TestDetectGaps:
    def test_no_gaps_back_to_back(self):
        """Back-to-back assignments with 0-day gap."""
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, today + timedelta(days=30), technician_id=tid)
        a2 = make_assignment(
            today + timedelta(days=31), today + timedelta(days=60), technician_id=tid,
        )
        gaps = detect_gaps([a1, a2], "Marcus Rivera")
        assert len(gaps) == 1
        assert gaps[0].gap_days == 0
        assert gaps[0].severity == GapSeverity.SEAMLESS

    def test_detects_small_gap(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, today + timedelta(days=30), technician_id=tid)
        # 3-day gap
        a2 = make_assignment(
            today + timedelta(days=34), today + timedelta(days=60), technician_id=tid,
        )
        gaps = detect_gaps([a1, a2], "Marcus Rivera")
        assert len(gaps) == 1
        assert gaps[0].gap_days == 3
        assert gaps[0].severity == GapSeverity.ACCEPTABLE

    def test_detects_large_gap(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, today + timedelta(days=30), technician_id=tid)
        # 20-day gap
        a2 = make_assignment(
            today + timedelta(days=51), today + timedelta(days=80), technician_id=tid,
        )
        gaps = detect_gaps([a1, a2], "Marcus Rivera")
        assert len(gaps) == 1
        assert gaps[0].gap_days == 20
        assert gaps[0].severity == GapSeverity.CRITICAL

    def test_skips_cancelled_assignments(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, today + timedelta(days=30), technician_id=tid)
        a_cancelled = make_assignment(
            today + timedelta(days=31), today + timedelta(days=40),
            technician_id=tid, status="Cancelled",
        )
        a3 = make_assignment(
            today + timedelta(days=50), today + timedelta(days=70), technician_id=tid,
        )
        gaps = detect_gaps([a1, a_cancelled, a3], "Marcus Rivera")
        # Should skip the cancelled one and find gap between a1 and a3
        assert len(gaps) == 1
        assert gaps[0].gap_days == 19

    def test_no_gaps_when_single_assignment(self):
        today = date.today()
        a = make_assignment(today, today + timedelta(days=30))
        gaps = detect_gaps([a], "Marcus Rivera")
        assert len(gaps) == 0

    def test_no_gaps_when_overlapping(self):
        """Overlapping assignments should not produce gaps."""
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, today + timedelta(days=30), technician_id=tid)
        a2 = make_assignment(
            today + timedelta(days=20), today + timedelta(days=50), technician_id=tid,
        )
        gaps = detect_gaps([a1, a2], "Marcus Rivera")
        assert len(gaps) == 0

    def test_multiple_gaps(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, today + timedelta(days=10), technician_id=tid)
        a2 = make_assignment(
            today + timedelta(days=15), today + timedelta(days=25), technician_id=tid,
        )
        a3 = make_assignment(
            today + timedelta(days=40), today + timedelta(days=60), technician_id=tid,
        )
        gaps = detect_gaps([a1, a2, a3], "Marcus Rivera")
        assert len(gaps) == 2
        assert gaps[0].gap_days == 4
        assert gaps[1].gap_days == 14


# ---------------------------------------------------------------------------
# Overlap Detection
# ---------------------------------------------------------------------------

class TestDetectOverlaps:
    def test_no_overlap(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, today + timedelta(days=30), technician_id=tid)
        a2 = make_assignment(
            today + timedelta(days=31), today + timedelta(days=60), technician_id=tid,
        )
        overlaps = detect_overlaps([a1, a2])
        assert len(overlaps) == 0

    def test_partial_overlap(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, today + timedelta(days=30), technician_id=tid)
        a2 = make_assignment(
            today + timedelta(days=20), today + timedelta(days=50), technician_id=tid,
        )
        overlaps = detect_overlaps([a1, a2])
        assert len(overlaps) == 1
        assert overlaps[0].overlap_type == OverlapType.PARTIAL_OVERLAP
        assert overlaps[0].overlap_days == 11  # days 20-30 inclusive

    def test_full_overlap(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, today + timedelta(days=60), technician_id=tid)
        a2 = make_assignment(
            today + timedelta(days=10), today + timedelta(days=30), technician_id=tid,
        )
        overlaps = detect_overlaps([a1, a2])
        assert len(overlaps) == 1
        assert overlaps[0].overlap_type == OverlapType.FULL_OVERLAP

    def test_same_dates(self):
        today = date.today()
        end = today + timedelta(days=30)
        tid = uuid.uuid4()
        a1 = make_assignment(today, end, technician_id=tid)
        a2 = make_assignment(today, end, technician_id=tid)
        overlaps = detect_overlaps([a1, a2])
        assert len(overlaps) == 1
        assert overlaps[0].overlap_type == OverlapType.SAME_DATES

    def test_skips_cancelled(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, today + timedelta(days=30), technician_id=tid)
        a2 = make_assignment(
            today + timedelta(days=10), today + timedelta(days=50),
            technician_id=tid, status="Cancelled",
        )
        overlaps = detect_overlaps([a1, a2])
        assert len(overlaps) == 0

    def test_open_ended_assignment_overlap(self):
        """Assignment with no end_date treated as extending to window end."""
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, None, technician_id=tid)
        a2 = make_assignment(today + timedelta(days=10), today + timedelta(days=50), technician_id=tid)
        overlaps = detect_overlaps([a1, a2])
        assert len(overlaps) == 1


# ---------------------------------------------------------------------------
# Overlap Resolution Proposals
# ---------------------------------------------------------------------------

class TestOverlapResolution:
    def test_active_vs_prebooked_adjusts_prebooked(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(
            today, today + timedelta(days=30), technician_id=tid,
            assignment_type="Active",
        )
        a2 = make_assignment(
            today + timedelta(days=20), today + timedelta(days=50),
            technician_id=tid, assignment_type="Pre-Booked",
        )
        overlap = MagicMock()
        overlap.assignment_a_id = str(a1.id)
        overlap.assignment_b_id = str(a2.id)
        overlap.overlap_days = 11

        proposal = propose_overlap_resolution(overlap, [a1, a2])
        assert proposal["action"] == "adjust_start"
        assert proposal["target_assignment_id"] == str(a2.id)

    def test_prebooked_vs_active_adjusts_prebooked(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(
            today, today + timedelta(days=30), technician_id=tid,
            assignment_type="Pre-Booked",
        )
        a2 = make_assignment(
            today + timedelta(days=20), today + timedelta(days=50),
            technician_id=tid, assignment_type="Active",
        )
        overlap = MagicMock()
        overlap.assignment_a_id = str(a1.id)
        overlap.assignment_b_id = str(a2.id)

        proposal = propose_overlap_resolution(overlap, [a1, a2])
        assert proposal["action"] == "adjust_end"
        assert proposal["target_assignment_id"] == str(a1.id)

    def test_both_active_manual_review(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(
            today, today + timedelta(days=30), technician_id=tid,
            assignment_type="Active",
        )
        a2 = make_assignment(
            today + timedelta(days=20), today + timedelta(days=50),
            technician_id=tid, assignment_type="Active",
        )
        overlap = MagicMock()
        overlap.assignment_a_id = str(a1.id)
        overlap.assignment_b_id = str(a2.id)
        overlap.overlap_days = 11
        overlap.project_a_name = "Project A"
        overlap.project_b_name = "Project B"

        proposal = propose_overlap_resolution(overlap, [a1, a2])
        assert proposal["action"] == "manual_review"

    def test_both_prebooked_adjusts_later(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(
            today, today + timedelta(days=30), technician_id=tid,
            assignment_type="Pre-Booked",
        )
        a2 = make_assignment(
            today + timedelta(days=20), today + timedelta(days=50),
            technician_id=tid, assignment_type="Pre-Booked",
        )
        overlap = MagicMock()
        overlap.assignment_a_id = str(a1.id)
        overlap.assignment_b_id = str(a2.id)

        proposal = propose_overlap_resolution(overlap, [a1, a2])
        assert proposal["action"] == "adjust_start"
        assert proposal["target_assignment_id"] == str(a2.id)


# ---------------------------------------------------------------------------
# Chain Continuity Validation
# ---------------------------------------------------------------------------

class TestChainContinuityValidation:
    def test_valid_chain(self):
        today = date.today()
        tid = uuid.uuid4()
        cid = str(uuid.uuid4())

        a1 = make_assignment(
            today, today + timedelta(days=30), technician_id=tid,
            chain_id=cid, chain_position=1,
        )
        a2 = make_assignment(
            today + timedelta(days=31), today + timedelta(days=60),
            technician_id=tid, chain_id=cid, chain_position=2,
            previous_assignment_id=str(a1.id),
        )
        a1.next_assignment_id = str(a2.id)

        issues = validate_chain_continuity([a1, a2], cid)
        # Should have no errors
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 0

    def test_out_of_order_detection(self):
        today = date.today()
        tid = uuid.uuid4()
        cid = str(uuid.uuid4())

        a1 = make_assignment(
            today + timedelta(days=30), today + timedelta(days=60),
            technician_id=tid, chain_id=cid, chain_position=1,
        )
        a2 = make_assignment(
            today, today + timedelta(days=29),
            technician_id=tid, chain_id=cid, chain_position=2,
        )

        issues = validate_chain_continuity([a1, a2], cid)
        out_of_order = [i for i in issues if i.issue_type == ContinuityIssueType.OUT_OF_ORDER]
        assert len(out_of_order) == 1

    def test_cancelled_in_chain(self):
        today = date.today()
        tid = uuid.uuid4()
        cid = str(uuid.uuid4())

        a1 = make_assignment(
            today, today + timedelta(days=30), technician_id=tid,
            chain_id=cid, chain_position=1,
        )
        a2 = make_assignment(
            today + timedelta(days=31), today + timedelta(days=60),
            technician_id=tid, chain_id=cid, chain_position=2,
            status="Cancelled",
        )

        issues = validate_chain_continuity([a1, a2], cid)
        cancelled = [i for i in issues if i.issue_type == ContinuityIssueType.CANCELLED_IN_CHAIN]
        assert len(cancelled) == 1

    def test_missing_end_date_mid_chain(self):
        today = date.today()
        tid = uuid.uuid4()
        cid = str(uuid.uuid4())

        a1 = make_assignment(
            today, None,  # No end date but not last
            technician_id=tid, chain_id=cid, chain_position=1,
        )
        a2 = make_assignment(
            today + timedelta(days=31), today + timedelta(days=60),
            technician_id=tid, chain_id=cid, chain_position=2,
        )

        issues = validate_chain_continuity([a1, a2], cid)
        missing = [i for i in issues if i.issue_type == ContinuityIssueType.MISSING_END_DATE]
        assert len(missing) == 1

    def test_missing_end_date_last_link_ok(self):
        """Last link in chain is allowed to have no end date."""
        today = date.today()
        tid = uuid.uuid4()
        cid = str(uuid.uuid4())

        a1 = make_assignment(
            today, today + timedelta(days=30),
            technician_id=tid, chain_id=cid, chain_position=1,
        )
        a2 = make_assignment(
            today + timedelta(days=31), None,  # No end date, but it's the last link
            technician_id=tid, chain_id=cid, chain_position=2,
        )

        issues = validate_chain_continuity([a1, a2], cid)
        missing = [i for i in issues if i.issue_type == ContinuityIssueType.MISSING_END_DATE]
        assert len(missing) == 0

    def test_large_gap_in_chain(self):
        today = date.today()
        tid = uuid.uuid4()
        cid = str(uuid.uuid4())

        a1 = make_assignment(
            today, today + timedelta(days=30),
            technician_id=tid, chain_id=cid, chain_position=1,
        )
        # 20-day gap
        a2 = make_assignment(
            today + timedelta(days=51), today + timedelta(days=80),
            technician_id=tid, chain_id=cid, chain_position=2,
        )

        issues = validate_chain_continuity([a1, a2], cid)
        gap_issues = [i for i in issues if i.issue_type == ContinuityIssueType.GAP]
        assert len(gap_issues) == 1

    def test_overlap_in_chain(self):
        today = date.today()
        tid = uuid.uuid4()
        cid = str(uuid.uuid4())

        a1 = make_assignment(
            today, today + timedelta(days=30),
            technician_id=tid, chain_id=cid, chain_position=1,
        )
        # Overlaps by 5 days
        a2 = make_assignment(
            today + timedelta(days=26), today + timedelta(days=60),
            technician_id=tid, chain_id=cid, chain_position=2,
        )

        issues = validate_chain_continuity([a1, a2], cid)
        overlap_issues = [i for i in issues if i.issue_type == ContinuityIssueType.OVERLAP]
        assert len(overlap_issues) == 1

    def test_broken_link_detection(self):
        today = date.today()
        tid = uuid.uuid4()
        cid = str(uuid.uuid4())
        wrong_id = uuid.uuid4()

        a1 = make_assignment(
            today, today + timedelta(days=30),
            technician_id=tid, chain_id=cid, chain_position=1,
        )
        a2 = make_assignment(
            today + timedelta(days=31), today + timedelta(days=60),
            technician_id=tid, chain_id=cid, chain_position=2,
            previous_assignment_id=str(wrong_id),  # Points to wrong assignment
        )

        issues = validate_chain_continuity([a1, a2], cid)
        broken = [i for i in issues if i.issue_type == ContinuityIssueType.BROKEN_LINK]
        assert len(broken) >= 1

    def test_empty_chain(self):
        issues = validate_chain_continuity([], "some-chain-id")
        assert len(issues) == 0


# ---------------------------------------------------------------------------
# Implicit Chain Detection
# ---------------------------------------------------------------------------

class TestImplicitChain:
    def test_detects_sequential_assignments(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, today + timedelta(days=30), technician_id=tid)
        a2 = make_assignment(
            today + timedelta(days=31), today + timedelta(days=60), technician_id=tid,
        )
        a3 = make_assignment(
            today + timedelta(days=61), today + timedelta(days=90), technician_id=tid,
        )

        chain = build_implicit_chain([a1, a2, a3], "Marcus Rivera")
        assert chain is not None
        assert len(chain.links) == 3
        assert chain.technician_name == "Marcus Rivera"

    def test_no_implicit_chain_single_assignment(self):
        today = date.today()
        a = make_assignment(today, today + timedelta(days=30))
        chain = build_implicit_chain([a], "Marcus Rivera")
        assert chain is None

    def test_no_implicit_chain_large_gaps(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, today + timedelta(days=10), technician_id=tid)
        # 30-day gap — too large for implicit chaining
        a2 = make_assignment(
            today + timedelta(days=41), today + timedelta(days=60), technician_id=tid,
        )
        chain = build_implicit_chain([a1, a2], "Marcus Rivera")
        assert chain is None

    def test_implicit_chain_with_small_gaps(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, today + timedelta(days=30), technician_id=tid)
        # 5-day gap — within acceptable threshold
        a2 = make_assignment(
            today + timedelta(days=36), today + timedelta(days=60), technician_id=tid,
        )
        chain = build_implicit_chain([a1, a2], "Marcus Rivera")
        assert chain is not None
        assert len(chain.links) == 2

    def test_implicit_chain_calculates_totals(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, today + timedelta(days=30), technician_id=tid)
        a2 = make_assignment(
            today + timedelta(days=31), today + timedelta(days=60), technician_id=tid,
        )

        chain = build_implicit_chain([a1, a2], "Marcus Rivera")
        assert chain is not None
        assert chain.total_booked_days == 59  # 30 + 29 (date diff arithmetic)
        assert chain.total_gap_days == 0

    def test_empty_list(self):
        chain = build_implicit_chain([], "Marcus Rivera")
        assert chain is None


# ---------------------------------------------------------------------------
# Data class property tests
# ---------------------------------------------------------------------------

class TestGapInfo:
    def test_gap_info_fields(self):
        from app.services.assignment_chaining import GapInfo
        gap = GapInfo(
            technician_id="tech-1",
            technician_name="Marcus Rivera",
            gap_start=date(2026, 4, 1),
            gap_end=date(2026, 4, 5),
            gap_days=5,
            severity=GapSeverity.ACCEPTABLE,
            previous_assignment_id="a-1",
            next_assignment_id="a-2",
            previous_project_name="Metro Build",
            next_project_name="Data Center Exp",
        )
        assert gap.gap_days == 5
        assert gap.severity == GapSeverity.ACCEPTABLE
        assert gap.previous_project_name == "Metro Build"


class TestOverlapInfo:
    def test_overlap_info_fields(self):
        from app.services.assignment_chaining import OverlapInfo
        ov = OverlapInfo(
            technician_id="tech-1",
            technician_name="Marcus Rivera",
            assignment_a_id="a-1",
            assignment_b_id="a-2",
            overlap_start=date(2026, 4, 10),
            overlap_end=date(2026, 4, 15),
            overlap_days=6,
            overlap_type=OverlapType.PARTIAL_OVERLAP,
        )
        assert ov.overlap_days == 6
        assert ov.overlap_type == OverlapType.PARTIAL_OVERLAP


class TestResolvedChain:
    def test_resolved_chain_validity(self):
        from app.services.assignment_chaining import ResolvedChain, ContinuityIssue
        chain = ResolvedChain(
            chain_id="c-1",
            technician_id="tech-1",
            technician_name="Marcus Rivera",
            chain_priority="Medium",
            links=[],
            is_valid=True,
        )
        assert chain.is_valid
        assert chain.total_gap_days == 0


# ---------------------------------------------------------------------------
# Integration-style tests (with mocked DB)
# ---------------------------------------------------------------------------

class TestCalculateRollingOffSoon:
    def test_identifies_technicians_without_next(self):
        """Should find techs ending soon with no follow-on assignment."""
        today = date.today()
        tid = uuid.uuid4()

        ending_assignment = make_assignment(
            today - timedelta(days=20),
            today + timedelta(days=5),
            technician_id=tid,
            status="Active",
        )
        ending_assignment.technician.full_name = "Sarah Chen"

        # Mock DB session
        db = MagicMock()
        mock_query = MagicMock()
        db.query.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = [ending_assignment]
        mock_query.first.return_value = None  # No next assignment

        results = calculate_rolling_off_soon(db, within_days=14, as_of=today)
        assert len(results) == 1
        assert results[0]["technician_name"] == "Sarah Chen"
        assert results[0]["has_next_assignment"] is False
        assert results[0]["days_remaining"] == 5


class TestBuildChainWithDB:
    def test_build_chain_empty(self):
        """build_chain with no matching assignments returns invalid chain."""
        db = MagicMock()
        mock_query = MagicMock()
        db.query.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = []

        chain = build_chain(db, "nonexistent-chain-id")
        assert chain.is_valid is False
        assert len(chain.continuity_issues) == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_assignments_without_end_dates_in_overlap_detection(self):
        """Open-ended assignments should be handled gracefully."""
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(today, None, technician_id=tid)
        a2 = make_assignment(today + timedelta(days=10), None, technician_id=tid)
        overlaps = detect_overlaps([a1, a2])
        assert len(overlaps) == 1

    def test_all_cancelled_assignments(self):
        today = date.today()
        tid = uuid.uuid4()
        a1 = make_assignment(
            today, today + timedelta(days=30), technician_id=tid, status="Cancelled",
        )
        a2 = make_assignment(
            today + timedelta(days=31), today + timedelta(days=60),
            technician_id=tid, status="Cancelled",
        )
        gaps = detect_gaps([a1, a2], "Marcus Rivera")
        assert len(gaps) == 0

        overlaps = detect_overlaps([a1, a2])
        assert len(overlaps) == 0

    def test_single_day_assignment(self):
        today = date.today()
        tid = uuid.uuid4()
        a = make_assignment(today, today, technician_id=tid)
        gaps = detect_gaps([a], "Marcus Rivera")
        assert len(gaps) == 0

    def test_gap_severity_boundary_values(self):
        assert _classify_gap(MAX_GAP_SEAMLESS_DAYS) == GapSeverity.SEAMLESS
        assert _classify_gap(MAX_GAP_ACCEPTABLE_DAYS) == GapSeverity.ACCEPTABLE
        assert _classify_gap(MAX_GAP_ACCEPTABLE_DAYS + 1) == GapSeverity.WARNING
        assert _classify_gap(MAX_GAP_WARNING_DAYS) == GapSeverity.WARNING
        assert _classify_gap(MAX_GAP_WARNING_DAYS + 1) == GapSeverity.CRITICAL
