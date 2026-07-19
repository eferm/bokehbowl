# bokehbowl

A small web app for mailing pictures, postcards, photos, or letters to people who
request one. People provide their name, email address, and postal address. The
operator confirms requests by email, prepares mailing batches, and exports address
labels.

**Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy, SQLite, Alembic, Jinja2, uv,
and ruff.

## Run locally

```sh
git clone <this repo> && cd bokehbowl
uv sync
uv run alembic upgrade head
SESSION_SECRET=dev ADMIN_PASSWORD=admin COOKIE_SECURE=false uv run uvicorn main:app --reload
```

Open http://localhost:8000. The default console mail backend prints sign-in codes
to the terminal. Visit http://localhost:8000/admin to sign in as the operator.

## Configure an instance

Place `index.html` or `privacy.html` in `instance/templates/` to customize those
pages. These templates extend the supplied layout; see
[`instance/templates/README.md`](instance/templates/README.md). An
`instance/favicon.svg` file replaces the default icon.

Review `/privacy` before opening the instance to signups.

## Deploy with Docker

Docker Compose is the supported deployment path.


```sh
cp .env.example .env
```

Copy `.env.example` to `.env` and set `SESSION_SECRET` and `ADMIN_PASSWORD` to
strong values. Set `OPERATOR_NAME` and `OPERATOR_EMAIL` for the privacy page and
signup notifications. `NOTIFY_EMAIL` sends those notifications elsewhere.

Set `MAIL_BACKEND=smtp` and the `SMTP_*` variables to send email through an SMTPS
provider. The example values use Cloudflare Email Service.

```sh
docker compose up -d --build
```

Compose binds the app to `127.0.0.1:8000`. Put an HTTPS reverse proxy or Cloudflare
Tunnel in front of it. The container applies Alembic migrations at startup. SQLite
data lives in `./data/`; copy that directory as part of your backup routine.

For a Cloudflare proxy, use Full (strict) TLS with an origin certificate, or use a
Cloudflare Tunnel. A rate-limiting rule for `POST /signup`, `POST /login`, and
`POST /admin/login` adds edge protection for public instances.

### Updates

Keep `.env`, `data/`, and `instance/` outside the repository's tracked files. After
pulling an update, rebuild and restart:

```sh
git pull
docker compose up -d --build
```

Back up `data/`, `.env`, and `instance/` before updates.

## Deploy on Cloudflare

Experimental support for Cloudflare Workers + D1 (experimental)

The Workers entrypoint is experimental. `worker.py` describes the required D1
database setup, bindings, secrets, and deployment command; `wrangler.toml` supplies
the Worker configuration.

## Usage Manual

### Create a mailing

At `/admin`, create a mailing and open it. The mailing page lists eligible recipients
and provides a CSV export for labels. Marking an item sent records the address used
for that mailing. Recipients joining after the mailing's creation appear separately
and can be included deliberately.

## Development

```sh
uv run pytest
uv run ruff check .
uv run ruff format .
uv run alembic revision --autogenerate -m "..."
```

Run the final command after changing the SQLAlchemy models.
