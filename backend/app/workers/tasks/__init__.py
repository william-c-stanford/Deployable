"""Celery task modules for Deployable reactive agents.

Each module handles a specific event category:
  - training: Proficiency advancement, training plan initialization
  - certification: Cert expiry handling, deployability recalc
  - document: Doc completeness checking, status updates
  - assignment: Assignment lifecycle, tech status updates
  - recommendation: Staffing recommendations, scoring, preference rules
  - batch: Nightly score refresh, cert expiry scans
"""
