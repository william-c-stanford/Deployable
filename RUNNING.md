# Running Deployable Locally

## Prerequisites

- Docker + Docker Compose installed
- An Anthropic API key (for chat AI — optional, falls back gracefully without it)

## 1. Set your API key (optional but recommended)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Or create a `.env` file at the repo root:

```
ANTHROPIC_API_KEY=sk-ant-...
```

## 2. Start the full stack

```bash
docker-compose up --build
```

This will automatically:

1. Start PostgreSQL + Redis
2. Run `alembic upgrade head` (schema migrations)
3. Seed the database (55 technicians, 10 projects, 6 partners, 18 skills)
4. Start FastAPI on port 8000
5. Start Celery worker + Celery beat scheduler
6. Start the React dev server on port 3000

## 3. Open the app

- **Frontend**: http://localhost:3000
- **API docs**: http://localhost:8000/docs

## 4. Run tests

Run separately against a test database (not inside Docker):

```bash
cd backend
pip install -r requirements.txt
pytest tests/ -v
```

## Startup timing

The `migrate` init container runs first and exits before FastAPI starts. The frontend waits for FastAPI to be healthy. Expect ~60–90 seconds on first `--build`, ~15–20 seconds on subsequent starts.

## Tear down

```bash
docker-compose down        # stop containers, keep DB data
docker-compose down -v     # stop + wipe all volumes (fresh start)
```
