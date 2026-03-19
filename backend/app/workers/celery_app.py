"""Celery application factory and configuration for Deployable workers."""

import os
from celery import Celery
from celery.schedules import crontab

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "deployable",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Task routing — separate queues for different priority levels
    task_routes={
        "app.workers.tasks.training.*": {"queue": "training"},
        "app.workers.tasks.certification.*": {"queue": "certification"},
        "app.workers.tasks.document.*": {"queue": "document"},
        "app.workers.tasks.assignment.*": {"queue": "assignment"},
        "app.workers.tasks.recommendation.*": {"queue": "recommendation"},
        "app.workers.tasks.forward_staffing.*": {"queue": "recommendation"},
        "app.workers.tasks.next_step.*": {"queue": "batch"},
        "app.workers.tasks.batch.*": {"queue": "batch"},
        "app.workers.tasks.partner_visibility.*": {"queue": "batch"},
        "app.workers.tasks.escalation.*": {"queue": "assignment"},
        "app.workers.tasks.headcount.*": {"queue": "project"},
        "app.workers.tasks.readiness.*": {"queue": "training"},
        "app.workers.tasks.transitional.*": {"queue": "training"},
        "app.workers.tasks.dispatcher.*": {"queue": "default"},
    },
    task_default_queue="default",
    # Reliability
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    # Result expiration (24 hours)
    result_expires=86400,
    # Rate limits
    task_annotations={
        "app.workers.tasks.batch.*": {"rate_limit": "5/m"},
    },
)

# Celery Beat schedule for recurring tasks
celery_app.conf.beat_schedule = {
    "nightly-score-refresh": {
        "task": "app.workers.tasks.batch.nightly_batch",
        "schedule": crontab(hour=2, minute=0),  # 2:00 AM UTC
        "args": [],
    },
    "nightly-readiness-reeval": {
        "task": "app.workers.tasks.readiness.batch_reevaluate_readiness",
        "schedule": crontab(hour=2, minute=30),  # 2:30 AM UTC, after score refresh
        "args": [],
    },
    "cert-expiry-scan": {
        "task": "app.workers.tasks.batch.cert_expiry_scan",
        "schedule": crontab(hour=6, minute=0),  # 6:00 AM UTC
        "args": [],
    },
    "partner-48h-visibility-scan": {
        "task": "app.workers.tasks.partner_visibility.scan_upcoming_assignments",
        "schedule": crontab(hour="*/6", minute=15),  # Every 6 hours at :15
        "args": [],
    },
    "escalation-scan": {
        "task": "app.workers.tasks.escalation.scan_overdue_confirmations",
        "schedule": crontab(minute="*/15"),  # Every 15 minutes — catches 24h window breaches
        "args": [],
    },
    "forward-staffing-scan": {
        "task": "app.workers.tasks.forward_staffing.forward_staffing_scan",
        "schedule": crontab(hour="*/6", minute=30),  # Every 6 hours at :30
        "args": [],
    },
    "transitional-state-scan": {
        "task": "app.workers.tasks.transitional.scan_transitional_states",
        "schedule": crontab(minute="*/10"),  # Every 10 minutes — resolve expired/condition-met states
        "args": [],
    },
    "nightly-next-step-batch": {
        "task": "app.workers.tasks.next_step.nightly_next_step_batch",
        "schedule": crontab(hour=2, minute=45),  # 2:45 AM UTC, after score refresh + readiness reeval
        "args": [],
    },
}

# Auto-discover tasks in all task modules
celery_app.autodiscover_tasks([
    "app.workers.tasks.training",
    "app.workers.tasks.certification",
    "app.workers.tasks.document",
    "app.workers.tasks.assignment",
    "app.workers.tasks.recommendation",
    "app.workers.tasks.batch",
    "app.workers.tasks.partner_visibility",
    "app.workers.tasks.escalation",
    "app.workers.tasks.forward_staffing",
    "app.workers.tasks.readiness",
    "app.workers.tasks.headcount",
    "app.workers.tasks.transitional",
    "app.workers.tasks.next_step",
])
