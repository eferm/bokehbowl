# Maintaining bokehbowl

The browser UI uses server-rendered Jinja2 templates, native HTML forms, and CSS.
JavaScript policy: none. Form mutations use POST followed by a 303 redirect.
Mark-sent redirects anchor to the row immediately above the recorded mailpiece
in the rendered order, keeping that row's action visible for processing upward.
The first row anchors to itself. Draft and recorded rows expose their HTML
anchor through the row view models.

Use `pyproject.toml` and `uv.lock` as the dependency inventory. Run Python tools
with `uv`. Application routes live in `bokehbowl/web.py` and
`bokehbowl/admin.py`; templates and CSS live alongside them. Database changes
belong in the SQLAlchemy models and Alembic migrations.
