#!/usr/bin/env bash
set -e

echo "==> Creating base schema via SQLAlchemy (idempotent)..."
python - <<'PYEOF'
from app.database import Base, engine
import app.models
Base.metadata.create_all(engine)
print("Schema ready.")
PYEOF

echo "==> Stamping Alembic to heads (marks all migrations as applied)..."
alembic stamp heads 2>/dev/null || true

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
