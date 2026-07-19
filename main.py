"""Container/self-host entrypoint. Run with: uvicorn main:app"""

from sqlalchemy import create_engine

from bokehbowl.app import create_app
from bokehbowl.config import load_config
from bokehbowl.mailer import build_mailer


config = load_config()
app = create_app(
    config=config,
    engine=create_engine(config.database_url),
    mailer=build_mailer(config.mail),
)
