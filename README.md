# ExpiryGuard

Lot-level inventory and expiry tracking for independent pharmacies (repo: project-management).

- **Stock in** by medicine, lot number and expiry date, with an optional unit cost (baht).
- **Dispense** takes stock from the earliest-expiring lot first (FEFO) and shows which lots were used.
- **Dashboard** groups lots by days to expiry: red (0-30), yellow (31-60), green (61-90) and
  "later" (over 90), plus a low-stock list. Any lot can be pulled (written off) with a reason.
- **Waste report** shows expired and written-off stock per month, in units and in baht.

SKU and lot number are case-insensitive and stored upper-case. All dates use the pharmacy's
business timezone (default Asia/Bangkok).

## Architecture

```
Browser (React + Vite + TypeScript, hosted on GitHub Pages)
   |  HTTPS, Bearer token, cross-site
   v
FastAPI + SQLAlchemy 2 (hosted on Render)
   |
   v
PostgreSQL (hosted on Neon)
```

- `frontend/`: single-page app. The token is kept in `sessionStorage` and sent as a Bearer header.
- `backend/app/`: `routers/` (HTTP), `services/inventory.py` (all stock logic), `models.py`, `security.py`.
- `backend/tests/`: pytest suite; runs on in-memory SQLite and never touches a real database.

## Backend environment variables

Set these in `backend/.env` locally (copy `backend/.env.example`) and in the host's environment
in production. Never commit `.env`.

| Name | Description |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy connection URL of the PostgreSQL database (psycopg 3 driver). |
| `SECRET_KEY` | Random string of at least 32 characters used to sign login tokens. |
| `CORS_ORIGIN` | The frontend's origin: scheme and host only, no path and no trailing slash. The app refuses to start otherwise. |
| `BUSINESS_TIMEZONE` | IANA timezone that defines "today" for expiry, dispensing and reports. Optional; default `Asia/Bangkok`. |

## Local setup

Backend (Python 3.12, a local PostgreSQL):

```
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env            # then fill in the values
.venv/bin/python -m app.init_db              # creates the tables (new database)
.venv/bin/python -m app.create_user <name>   # asks for a password (min 12 characters)
.venv/bin/uvicorn app.main:app --reload
```

Frontend (Node 22):

```
cd frontend
npm ci
npm run dev        # proxies /api to the local backend on port 8000
```

For local use set `CORS_ORIGIN` to the Vite dev origin shown by `npm run dev`.

### When to run `python -m app.migrate`

Only on a database that was created **before v0.3**: it adds the unit-cost and reason columns.
It is idempotent and safe to re-run. A new database created with `app.init_db` does not need it.
v0.4 has no schema changes.

## Tests

```
cd backend
.venv/bin/pytest -q
```

`tests/test_concurrency_pg.py` is skipped unless `TEST_PG_URL` is set. It proves row locking on
a real PostgreSQL inside a throwaway schema; read the file header first and never point it at
the live database.

The frontend has no automated tests; `npm run build` type-checks it.

## Deploying

**Backend on Render (web service)**

- Root directory: `backend`
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Set the four environment variables above. `CORS_ORIGIN` is the GitHub Pages origin
  (scheme and host only, without the repository path).
- First deploy: run `python -m app.init_db` and `python -m app.create_user <name>` once
  against the production database.

**Frontend on GitHub Pages**

- Repository Settings > Pages > Source: **GitHub Actions**.
- Repository Settings > Secrets and variables > Actions > Variables: add `API_URL` with the
  backend's base URL, **no trailing slash and no `/api`**. The workflow fails if it is empty.
- Pushing to `main` runs `.github/workflows/deploy.yml`, which builds and publishes `frontend/`.

## Known limitations

- **Stateless logout**: logging out discards the token in the browser; the token itself stays
  valid until it expires (2 hours).
- **In-memory login rate limiter**: counters reset when the server restarts and are not shared
  between instances.
- **Free-tier cold start**: the backend sleeps when idle and takes up to about a minute to wake.
  The app shows "Server is waking up" and retries read requests automatically.
- No Alembic; schema upgrades are the static `app.migrate` script.

## Demo tip

A few minutes before presenting, open the API health URL (`<backend URL>/api/health`) in a
browser tab and wait for `{"status":"ok"}`. That wakes the server so the first login is instant.
