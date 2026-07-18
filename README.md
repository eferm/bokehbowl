# bokehbowl

A tiny web app for mailing postcards to people who ask for one. People sign up with
their name, email, and postal address; you (the admin) see a queue of who to send to,
mark postcards sent, and export addresses for printing. Free of charge, just for fun.

**Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 · SQLite · Alembic · Jinja2 · uv · ruff

**Auth:** passwordless — a 6-digit code emailed on sign-up/sign-in (which doubles as
email verification). No passwords stored, no third-party identity provider.

## Run it locally

```sh
git clone <this repo> && cd bokehbowl
uv sync
uv run alembic upgrade head
SESSION_SECRET=dev ADMIN_PASSWORD=admin COOKIE_SECURE=false uv run uvicorn main:app --reload
```

Open http://localhost:8000 — sign-in codes are printed to the terminal (the `console`
mail backend, the default). The admin lives at http://localhost:8000/admin.

## Configuration

Everything is environment variables — see `.env.example`. Required: `SESSION_SECRET`
(any long random string) and `ADMIN_PASSWORD`. To send real email, set
`MAIL_BACKEND=smtp` plus the `SMTP_*` variables; the defaults in `.env.example` point
at Cloudflare Email Service's SMTP endpoint, but any SMTPS provider works.

## Deploy

The Dockerfile is the deploy story. It runs database migrations on boot, then serves
on port 8000.
Whatever host you pick, put your DNS (e.g. Cloudflare) in front for HTTPS.

**Railway / Render** — create a project from this repo (both auto-detect the
Dockerfile), attach a volume at `/app/data`, set the env vars, deploy. Pushes to the
repo auto-deploy from then on.

**Fly.io** — `fly launch` (add a volume for `/app/data` when prompted), `fly secrets set
SESSION_SECRET=... ADMIN_PASSWORD=...`, then `fly deploy`.

**Self-host (any VPS)** — `cp .env.example .env`, edit it, then `docker compose up -d`.
The SQLite database lands in `./data/`; back it up by copying that directory (or point
[Litestream](https://litestream.io/) at it for continuous replication).

**Cloudflare Workers + D1 (experimental)** — the app is written to be compatible with
Cloudflare's Python Workers runtime: pure-Python dependencies, sync SQLAlchemy,
SQLite-dialect SQL (D1 is SQLite), stateless signed-cookie sessions, no filesystem
access in app code. See `worker.py` and `wrangler.toml`. Python Workers are in open
beta, so this path is best-effort — the container path above is the supported one.

## Make it yours

Each instance names its operator: set `OPERATOR_NAME` and `OPERATOR_CONTACT` and they
appear in the footer, the `/about` page, and the `/privacy` page. For a fully custom
front or about page, drop templates into `instance/templates/` — they shadow the
defaults (see the README in that directory). Forks commit their `instance/` folder;
docker-compose users can just edit it in place (it's mounted into the container).

The default `/privacy` page honestly describes what this app does (and doesn't do)
with data, naming your `OPERATOR_*` values as the responsible party — review it once
before going live, since you're the one collecting addresses.

### Keeping your instance up to date

All per-instance state lives in channels this repo never touches: environment
variables, `data/` (gitignored), and your own files in `instance/templates/`. Stay
inside those and updating is conflict-free:

- **Fork workflow** (needed for Railway/Render, which deploy from your repo): commit
  your `instance/` files to your fork, then use GitHub's *Sync fork* (or merge
  `upstream/main`) to pull updates — your platform redeploys.
- **No-fork workflow** (docker-compose): your `instance/` files and `.env` are
  untracked, so updating is `git pull && docker compose up -d --build`. Back up
  `instance/` and `.env` yourself.

**Database upgrades are automatic** in both flows: the container runs
`alembic upgrade head` on every boot, applying any new schema migrations before the
app serves (and doing nothing when there are none). If you run without Docker, that
command is yours to run after pulling: `uv sync && uv run alembic upgrade head`,
then restart. Either way, copying `data/bokehbowl.db` aside before an upgrade is
cheap insurance.

Editing the app's own files instead means ordinary merge-conflict life — allowed, but
no longer guaranteed painless.

If you're hosting from this repo itself (rather than a fork), keep `main` generic and
commit your instance files on a `deploy` branch; point your host at that branch and
update it with `git merge main`. Never merge the deploy branch back into `main` —
that's how your personal about page becomes everyone's default.

## Admin & data model

`/admin` shows the raw database tables, one view per table (columns come straight from
the schema), each exportable as CSV:

- **recipients** — one row per person, current state. *Unregister* here is a soft
  delete (timestamp); people can also unregister/rejoin themselves from their account
  page.
- **recipient_versions** — append-only history: one row per state a recipient has ever
  been in (written on signup and on every real change; a no-op save appends nothing).
  A version is valid from its `valid_from` until the next version's.
- **postcards** — one row per postcard design or print run ("sailboat photo") that
  gets sent to many people.
- **sendings** — one row per physical card mailed: which postcard, to which
  recipient, at which exact address version. A person can receive each postcard once
  (unique constraint).

### Sending a batch

Create a postcard on the postcards view, then open it: the detail page shows **To
send** — everyone eligible (verified, not unregistered) who hasn't received this
postcard — with addresses to copy and a CSV export for labels. *Mark sent* records
one sending pinned to the person's current address version; *Undo* fixes a
mis-click. People who unregister drop out of To send automatically. People who signed
up *after* the postcard was created aren't suggested by default — they sit in a
collapsed "Signed up after this postcard" section, sendable by explicit choice.

## Development

```sh
uv run pytest        # tests
uv run ruff check .  # lint
uv run ruff format . # format
uv run alembic revision --autogenerate -m "..."  # after changing models in bokehbowl/db.py
```

Layout: `bokehbowl/` is the app (config → db → auth/mailer → web/admin routes → app
factory), `main.py` is the container entrypoint, `migrations/` is Alembic (configured
in `pyproject.toml`). Templates are plain semantic HTML — a design pass is
deliberately still to come.
