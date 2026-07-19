# Per-instance customization

This directory holds a deployment's own content, kept apart from the application
so upgrades never touch it. Commit its contents if you run from a fork, or mount
the directory into the container (`./instance:/app/instance`, already configured
in `compose.yaml`). Restart the app after adding, replacing, or removing
instance files.

## `templates/`

Any template placed in `instance/templates/` shadows the default of the same
name in `bokehbowl/templates/`. The intended ones to customize:

- `index.html` — your front page
- `privacy.html` — if the default doesn't describe your instance accurately

Keep `{% extends "base.html" %}` at the top to inherit the layout.

Smaller customizations don't need templates at all — set the `OPERATOR_NAME` and
`OPERATOR_EMAIL` environment variables and the defaults will use them.

## `static/`

Files in `instance/static/` shadow the defaults served under
`bokehbowl/static/`. A `background.webp` placed there becomes the photograph
behind the public pages; without one, the pages use the built-in CSS gradient.
An `og.jpg` (1200×630) replaces the link-preview image and a `favicon.svg`
replaces the default mailbox icon, the same way.
