#!/usr/bin/env bash
set -e

echo "==> Creating base schema via SQLAlchemy (idempotent)..."
python - <<'PYEOF'
from app.database import Base, engine
import app.models
Base.metadata.create_all(engine)
print("Schema ready.")
PYEOF

echo "==> Running Alembic migrations..."
alembic upgrade heads || echo "Migration skipped (already at head or non-fatal error)"

echo "==> Seeding database..."
python - <<'PYEOF'
from app.database import SessionLocal
from app.seeds.loader import seed_all
db = SessionLocal()
try:
    seed_all(db)
    print("Seed complete.")
except Exception as exc:
    db.rollback()
    print(f"Seed warning (non-fatal): {exc}")
finally:
    db.close()
PYEOF

echo "==> Starting application..."
exec "$@"
