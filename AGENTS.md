# Conventions

The browser UI uses server-rendered Jinja2 templates, native HTML forms, and
CSS. JavaScript policy: none. Form mutations use POST followed by a 303
redirect.

Use `pyproject.toml` and `uv.lock` as the dependency inventory. Run Python tools
with `uv`. Application routes live in `bokehbowl/web.py` and
`bokehbowl/admin.py`; templates and CSS live alongside them. Database changes
belong in the SQLAlchemy models and Alembic migrations.
