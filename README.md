# bokehbowl

A small web app for mailing pictures, postcards, photos, or letters to people
who request one. People provide their name, email address, and postal address.
The operator confirms requests by email, prepares mailing batches, and exports
address labels.

**Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy, SQLite, Alembic, Jinja2,
uv, and ruff.

## Run locally

```sh
git clone <this repo> && cd bokehbowl
uv sync
uv run alembic upgrade head
SESSION_SECRET=dev ADMIN_PASSWORD=admin COOKIE_SECURE=false uv run uvicorn main:app --reload
```

Open http://localhost:8000. The default console mail backend prints sign-in
codes to the terminal. Visit http://localhost:8000/admin to sign in as the
operator.

## Configure an instance

Place `index.html` or `privacy.html` in `instance/templates/` to customize those
pages. These templates extend the supplied layout; see
[`instance/README.md`](instance/README.md).

Files in `instance/static/` shadow the served defaults. A `background.webp`
dropped there becomes the backdrop photograph on the public pages — without one,
the pages use the built-in gradient. A `favicon.svg` there overrides the default
icon, and an `og.jpg` (1200×630) the link-preview image. Restart the app after
changing instance files.

Review `/privacy` before opening the instance to signups.

## Deploy with Docker

Docker Compose is the supported deployment path.

```sh
cp .env.example .env
```

Copy `.env.example` to `.env` and set `SESSION_SECRET` and `ADMIN_PASSWORD` to
strong values. Set `OPERATOR_NAME` and `OPERATOR_EMAIL` for the privacy page and
signup notifications. `NOTIFY_EMAIL` sends those notifications elsewhere.

Set `MAIL_BACKEND=smtp` and the `SMTP_*` variables to send email through an
SMTPS provider. The example values use Cloudflare Email Service.

```sh
docker compose up -d --build
```

Compose binds the app to `127.0.0.1:8000`. Put an HTTPS reverse proxy or
Cloudflare Tunnel in front of it. The container applies Alembic migrations at
startup. SQLite data lives in `./data/`; copy that directory as part of your
backup routine.

Uvicorn runs with `--proxy-headers` and takes the client IP and scheme from
`X-Forwarded-*`. `FORWARDED_ALLOW_IPS` names the proxy hops trusted to set those
headers (default: loopback); the compose file sets it to the Docker network
ranges. On other hosting, set it to the address the platform's proxy connects
from. The client IP feeds the per-IP throttle on admin login.

For a Cloudflare proxy, use Full (strict) TLS with an origin certificate, or use
a Cloudflare Tunnel. A rate-limiting rule for `POST /signup`, `POST /login`, and
`POST /admin/login` adds edge protection for public instances.

### Updates

Keep `.env`, `data/`, and `instance/` outside the repository's tracked files.
After pulling an update, rebuild and restart:

```sh
git pull
docker compose up -d --build
```

Back up `data/`, `.env`, and `instance/` before updates.

## Usage Manual

### Create an edition

At `/admin`, create an edition and open it. The edition page lists eligible
users and provides a CSV export for labels. Marking an item sent records the
address used for that mailpiece. Users joining after the edition's creation
appear separately and can be included deliberately. Closing an edition stops
all further sends; reopening resumes them.

### Data model

- **users** — one row per person: their email identity and subscription
  status (`pending`, `active`, `unsubscribed`).
- **addresses** — every postal address a user has had, append-only. A row
  with `derived_from_id` set is a validated correction of the manual entry it
  points at. Mail uses the validated derivative when one exists; the account
  page always shows the user's own latest manual entry.
- **editions** — one print run (a postcard design, a photo, a letter), open
  or closed.
- **mailpieces** — one physical piece of mail: an edition sent to one user,
  pinned to the exact address row written on the envelope.

## Development

```sh
uv run pytest
uv run ruff check .
uv run ruff format .
uv run alembic revision --autogenerate -m "..."
```

Run the final command after changing the SQLAlchemy models.

### Rehearse a migration against production data

`tests/test_migration_prod.py` is skipped unless `BOKEHBOWL_PROD_DUMP` points
at a copy of the production database. It migrates a copy (the dump is never
mutated), asserts the data survived intact, and boots the app against the
migrated copy.

```sh
ssh yourserver "sqlite3 /path/to/data/bokehbowl.db '.backup /tmp/snapshot.db'"
scp yourserver:/tmp/snapshot.db /tmp/bokehbowl-before.db
BOKEHBOWL_PROD_DUMP=/tmp/bokehbowl-before.db uv run pytest tests/test_migration_prod.py
```

To inspect and click through locally, make a second copy, migrate it, and run
the app against it (four slashes for an absolute path):

```sh
cp /tmp/bokehbowl-before.db /tmp/bokehbowl-after.db
DATABASE_URL=sqlite:////tmp/bokehbowl-after.db uv run alembic upgrade head
DATABASE_URL=sqlite:////tmp/bokehbowl-after.db \
SESSION_SECRET=dev ADMIN_PASSWORD=admin COOKIE_SECURE=false \
uv run uvicorn main:app --reload
```

Open both files side by side in TablePlus. SQLite `ATTACH` lets one query tab
compare them — e.g. mailpieces whose new address fields differ from the
version they came from (expect zero rows):

```sql
ATTACH '/tmp/bokehbowl-before.db' AS before;
ATTACH '/tmp/bokehbowl-after.db' AS after;

SELECT m.id
FROM after.mailpieces m
JOIN before.mailpieces om        ON om.id = m.id
JOIN before.recipient_versions v ON v.id = om.recipient_version_id
JOIN after.addresses a           ON a.id = m.address_id
WHERE a.addressee    IS NOT v.name
   OR a.address_line1 IS NOT v.address_line1
   OR a.city         IS NOT v.city
   OR a.postal_code  IS NOT v.postal_code
   OR a.country      IS NOT v.country;
```

Stop uvicorn before re-copying or re-migrating the `after` file.
