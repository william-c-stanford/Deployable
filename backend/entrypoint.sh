#!/usr/bin/env bash
set -e

echo "==> Running Alembic migrations..."
alembic upgrade heads

echo "==> Seeding database..."
python -c "
from app.database import SessionLocal
from app.seeds.loader import seed_all
db = SessionLocal()
try:
    seed_all(db)
    print('Seed complete.')
except Exception as exc:
    db.rollback()
    print(f'Seed warning (non-fatal): {exc}')
finally:
    db.close()
"

echo "==> Starting application..."
exec "$@"
