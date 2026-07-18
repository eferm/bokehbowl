"""EXPERIMENTAL Cloudflare Workers entrypoint — the container path (main.py) is primary.

Python Workers are in open beta. The app itself is written to be Workers-compatible
(pure-Python deps, sync SQLAlchemy, no filesystem writes, stateless sessions), so this
entrypoint is expected to work but is not covered by CI. To try it:

  1. uv add sqlalchemy-cloudflare-d1
  2. Create a D1 database and apply the schema to it from your machine
     (alembic upgrade head with DATABASE_URL pointing at the D1 REST dialect).
  3. Configure wrangler.toml with the D1 binding and secrets, then: pywrangler deploy

See https://developers.cloudflare.com/workers/languages/python/
"""

from bokehbowl.app import create_app
from bokehbowl.config import AppConfig, SmtpMail
from bokehbowl.mailer import build_mailer


def build_app(env):
    from sqlalchemy import create_engine

    config = AppConfig(
        database_url="cloudflare_d1://",
        session_secret=env.SESSION_SECRET,
        admin_password=env.ADMIN_PASSWORD,
        cookie_secure=True,
        mail=SmtpMail(
            host="smtp.mx.cloudflare.net",
            port=465,
            username=env.SMTP_USERNAME,
            password=env.SMTP_PASSWORD,
            sender=env.MAIL_SENDER,
        ),
        operator_name=env.OPERATOR_NAME,
        operator_contact=env.OPERATOR_CONTACT,
        commit=getattr(env, "GIT_COMMIT", None),
    )
    engine = create_engine(config.database_url, connect_args={"binding": env.DB})
    return create_app(config=config, engine=engine, mailer=build_mailer(config.mail))
