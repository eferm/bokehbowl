# Maintaining bokehbowl

The browser UI uses server-rendered Jinja2 templates, native HTML forms, and CSS.
JavaScript policy: none. Form mutations use POST followed by a 303 redirect.
Mark-sent redirects use a `focus` query parameter to select the result row for native
HTML `autofocus` with `tabindex="-1"`. Redirects clear the URL fragment so
fragment navigation yields to focus navigation.

Use `pyproject.toml` and `uv.lock` as the dependency inventory. Run Python tools
with `uv`. Application routes live in `bokehbowl/web.py` and
`bokehbowl/admin.py`; templates and CSS live alongside them. Database changes
belong in the SQLAlchemy models and Alembic migrations.
