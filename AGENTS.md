# Maintaining bokehbowl

The browser UI uses server-rendered Jinja2 templates, native HTML forms, and CSS.
JavaScript policy: none. Form mutations use POST followed by a 303 redirect.
Mailing rows use one-based HTML IDs in rendered order. Mark-sent form actions
carry the preceding row's fragment; the first row targets itself. A plain 303
redirect preserves that fragment through native browser navigation, as specified
in [RFC 9110 §10.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.2).

Use `pyproject.toml` and `uv.lock` as the dependency inventory. Run Python tools
with `uv`. Application routes live in `bokehbowl/web.py` and
`bokehbowl/admin.py`; templates and CSS live alongside them. Database changes
belong in the SQLAlchemy models and Alembic migrations.
