"""Add training models — programs, enrollment, hours logs, advancement gates.

Revision ID: 001_training
Revises: None
Create Date: 2026-03-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001_training'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enums
    advancement_level_enum = postgresql.ENUM(
        'Apprentice', 'Intermediate', 'Advanced',
        name='advancement_level_enum', create_type=True,
    )
    enrollment_status_enum = postgresql.ENUM(
        'Active', 'Completed', 'Paused', 'Withdrawn',
        name='enrollment_status_enum', create_type=True,
    )
    hours_log_source_enum = postgresql.ENUM(
        'Timesheet', 'Classroom', 'Online', 'Field Training', 'Assessment', 'Manual',
        name='hours_log_source_enum', create_type=True,
    )

    advancement_level_enum.create(op.get_bind(), checkfirst=True)
    enrollment_status_enum.create(op.get_bind(), checkfirst=True)
    hours_log_source_enum.create(op.get_bind(), checkfirst=True)

    # Training Programs
    op.create_table(
        'training_programs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(200), unique=True, nullable=False),
        sa.Column('slug', sa.String(200), unique=True, nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('total_hours_required', sa.Float, default=0.0),
        sa.Column('apprentice_hours_min', sa.Float, default=0.0),
        sa.Column('intermediate_hours_threshold', sa.Float, default=100.0),
        sa.Column('advanced_hours_threshold', sa.Float, default=300.0),
        sa.Column('skill_category_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('skill_categories.id'), nullable=True),
        sa.Column('is_active', sa.Boolean, default=True),
        sa.Column('display_order', sa.Integer, default=0),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('updated_at', sa.DateTime, nullable=False),
    )

    # Training Enrollments
    op.create_table(
        'training_enrollments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('technician_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('technicians.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('program_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('training_programs.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('advancement_level', advancement_level_enum, nullable=False, default='Apprentice'),
        sa.Column('total_hours_logged', sa.Float, default=0.0),
        sa.Column('status', enrollment_status_enum, nullable=False, default='Active'),
        sa.Column('enrolled_at', sa.DateTime, nullable=False),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.Column('last_advancement_check', sa.DateTime, nullable=True),
        sa.Column('last_advanced_at', sa.DateTime, nullable=True),
        sa.UniqueConstraint('technician_id', 'program_id', name='uq_enrollment_tech_program'),
    )
    op.create_index('ix_enrollment_tech_status', 'training_enrollments',
                    ['technician_id', 'status'])

    # Training Hours Logs
    op.create_table(
        'training_hours_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('technician_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('technicians.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('enrollment_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('training_enrollments.id', ondelete='CASCADE'),
                  nullable=True, index=True),
        sa.Column('skill_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('skills.id', ondelete='SET NULL'),
                  nullable=True, index=True),
        sa.Column('timesheet_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('timesheets.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('hours', sa.Float, nullable=False),
        sa.Column('logged_date', sa.Date, nullable=False),
        sa.Column('source', hours_log_source_enum, nullable=False, default='Manual'),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('approved', sa.Boolean, default=False),
        sa.Column('approved_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('approved_at', sa.DateTime, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.CheckConstraint('hours > 0', name='ck_hours_positive'),
    )
    op.create_index('ix_hours_log_tech_date', 'training_hours_logs',
                    ['technician_id', 'logged_date'])
    op.create_index('ix_hours_log_enrollment', 'training_hours_logs',
                    ['enrollment_id', 'logged_date'])

    # Advancement Gate Configs
    op.create_table(
        'advancement_gate_configs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('program_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('training_programs.id', ondelete='CASCADE'),
                  nullable=True, index=True),
        sa.Column('skill_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('skills.id', ondelete='CASCADE'),
                  nullable=True, index=True),
        sa.Column('target_level', advancement_level_enum, nullable=False),
        sa.Column('certification_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('certifications.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('min_hours_override', sa.Float, nullable=True),
        sa.Column('is_mandatory', sa.Boolean, default=True),
        sa.Column('gate_description', sa.Text, nullable=True),
        sa.Column('is_active', sa.Boolean, default=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('updated_at', sa.DateTime, nullable=False),
        sa.UniqueConstraint('program_id', 'skill_id', 'target_level', 'certification_id',
                           name='uq_gate_config_program_skill_level_cert'),
    )

    # Update proficiency_level_enum: rename 'Beginner' to 'Apprentice'
    # Note: This requires careful handling in PostgreSQL
    # We add the new value and then update existing rows
    op.execute("ALTER TYPE proficiency_level_enum ADD VALUE IF NOT EXISTS 'Apprentice'")
    op.execute("""
        UPDATE technician_skills
        SET proficiency_level = 'Apprentice'
        WHERE proficiency_level = 'Beginner'
    """)


def downgrade() -> None:
    op.drop_table('advancement_gate_configs')
    op.drop_table('training_hours_logs')
    op.drop_table('training_enrollments')
    op.drop_table('training_programs')

    op.execute("DROP TYPE IF EXISTS hours_log_source_enum")
    op.execute("DROP TYPE IF EXISTS enrollment_status_enum")
    # Don't drop advancement_level_enum as it may be shared

    # Revert proficiency level
    op.execute("""
        UPDATE technician_skills
        SET proficiency_level = 'Beginner'
        WHERE proficiency_level = 'Apprentice'
    """)
