#!/usr/bin/env bash
set -e

echo "==> Creating schema via SQLAlchemy..."
python - <<'PYEOF'
from app.database import Base, engine
import app.models
Base.metadata.create_all(engine)
print("Schema created.")
PYEOF

echo "==> Stamping Alembic to head..."
alembic stamp head

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

echo "==> Done."
