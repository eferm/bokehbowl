# Per-instance customization

Any template placed in this directory shadows the default of the same name in
`bokehbowl/templates/`. The intended ones to customize:

- `index.html` — your front page
- `privacy.html` — only if the default doesn't describe your instance accurately

Keep `{% extends "base.html" %}` at the top to inherit the layout. Commit your
versions if you run from a fork, or mount this directory into the container
(`./instance:/app/instance`, already configured in compose.yaml).

Smaller customizations don't need templates at all — set the `OPERATOR_NAME` and
`OPERATOR_EMAIL` environment variables and the defaults will use them. Files in
`instance/static/` (such as `favicon.svg`, `background.webp`, and `og.jpg`)
shadow the packaged defaults in `bokehbowl/static/`.

Everything in `instance/` is operator content: it stays yours and sits outside
the repository's MIT license.
