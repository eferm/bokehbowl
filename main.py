"""Container/self-host entrypoint. Run with: uvicorn main:app"""

from bokehbowl.app import create_app
from bokehbowl.config import load_config
from bokehbowl.db import build_engine
from bokehbowl.mailer import build_mailer


config = load_config()
app = create_app(
    config=config,
    engine=build_engine(config.database_url),
    mailer=build_mailer(config.mail),
)
