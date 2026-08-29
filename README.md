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
mkdir -p data
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
signup notifications; `NOTIFY_EMAIL` sends those notifications elsewhere.
`OPERATOR_TZ` is the operator's IANA timezone, such as `America/New_York`.

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
from.

For a Cloudflare proxy, use Full (strict) TLS with an origin certificate, or use
a Cloudflare Tunnel.

### Updates

Keep `.env`, `data/`, and `instance/` outside the repository's tracked files.
After pulling an update, rebuild and restart:

```sh
git pull
docker compose up -d --build
```

Back up `data/`, `.env`, and `instance/` before updates.

## Usage Manual

### Sign up and sign in

The signup form and the sign-in form both send a 6-digit code, and the code
is what creates the session. Signing up with an email that already has an
account signs that account in, showing the address on file with a note that
the account was already there; the account page is where addresses change.

### Create an edition

At `/admin`, create an edition. It gets one mailpiece draft for every currently
subscribed user. Add or remove drafts on the edition page, normalize addresses
marked Needs review, export the Ready addresses for labels, then mark each draft
sent to record its print version and local mailing date.

Marking a mailpiece returned creates a replacement draft. Another draft for the
same user can also be added directly. A normalized address that produced
returned mail cannot be sent again; normalize a changed address before marking
the replacement sent. Unsubscribed users keep their drafts, but those drafts
cannot be marked sent.

### Data model

- **users** — one row per verified email identity. `created_at` is the
  verification moment; `unsubscribed_at` set means mail stops. A signup's
  address travels in the form until verification creates the user and their
  first address in one transaction.
- **addresses** — every postal address a user entered, append-only; the
  latest row is current and is what the account page shows. Countries use
  CLDR's English territory names.
- **normalized_addresses** — operator-approved print versions, each pinned to
  one address row, append-only. An envelope prints the latest normalized
  version of its address; an address with one is ready to send.
- **editions** — one print run (a postcard design, a photo, a letter).
  `deleted_at` set archives it from operational routes while the raw admin
  table keeps the row visible.
- **drafts** — planned physical copies. New editions seed one draft
  for every currently subscribed user; operators can add or remove drafts, and
  the same user can have several.
- **mailpieces** — one physical piece of mail: an edition sent to one user,
  pinned to the normalized row printed on the envelope. `sent_at` is the UTC
  audit timestamp for the click; `sent_on` is the durable operator-local
  mailing date. An edition can have multiple mailpieces for the same user.
- **returns** — one returned-mail fact per physical mailpiece, with the UTC
  time the operator recorded its return.

### Invariants

Each invariant lives at a named enforcement layer:

- One user per email — `UNIQUE` on `users.email`.
- Each mailpiece draft has its own identity — `drafts.id` is its
  primary key, so an edition can contain several drafts for the same user.
- One return per physical mailpiece — `returns.mailpiece_id` is both its
  primary key and a foreign key to `mailpieces.id`.
- Every user has an address — registration creates the user and their
  first address in one transaction.
- Every envelope prints an operator-approved form —
  `mailpieces.normalized_address_id` is non-null, and marking a draft sent
  requires the user's current normalized row without a recorded return before
  writing.
- A normalized address prints only while its raw address is the user's
  latest — each normalized row is pinned to one address row by `address_id`.
- Login codes are single-use — consuming a code marks it as consumed.
- Foreign keys hold at runtime, and deleting a user cascades down the
  user-rooted chain — the engine factory turns `PRAGMA foreign_keys` on for
  every connection.

## Development

### Country data

The app reads one country name per line from
`bokehbowl/resources/countries.txt`. CLDR's English locale data is archived at
`vendor/unicode-org/en.xml`, with its retrieval metadata, license, and
checksum in `vendor/manifest.json`. Regenerate the runtime data with:

```sh
uv run scripts/parse_cldr_countries.py
```

```sh
uv run pytest
uv run ruff check .
uv run ruff format .
uv audit
uv run alembic revision --autogenerate -m "..."
```

`uv audit` checks the locked dependencies against known advisories; run it when
updating `uv.lock`. Run the final command after changing the SQLAlchemy models.
