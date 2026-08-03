# TODO

## Use Cloudflare Access for passwordless admin authentication

Replace Bokehbowl's separate admin password screen and database-backed admin
sessions with Cloudflare Access authentication for every `/admin` route. The
Cloudflare Access application is scoped to `/admin*` and permits only the
operator's identity.

### Requirements

- Read the Access application token from the `Cf-Access-Jwt-Assertion` request
  header on every `/admin` request.
- Validate the JWT signature using the Cloudflare Access JWKS endpoint; never
  trust the header or its decoded claims without signature verification.
- Validate the expected issuer, Application Audience (AUD) tag, expiration,
  not-before time, and token type. Optionally require the configured operator
  email as an additional check.
- Fail closed with `403` when the token is missing, invalid, expired, or cannot
  be verified.
- Cache signing keys safely while allowing Cloudflare's key rotation to take
  effect.
- Configure the team domain, AUD tag, and expected operator email through
  environment variables. Do not commit tokens or other credentials.
- Remove `ADMIN_PASSWORD`, `AdminSession`, the `/admin/login` password form, and
  the app-specific `/admin/logout` session handling after Access verification is
  in place.
- Make the admin logout control point to `/cdn-cgi/access/logout`.
- Preserve the existing router-level CSRF protection for all state-changing
  admin actions; Access authentication does not replace CSRF protection.
- Document the required Cloudflare Access `/admin*` application and environment
  configuration in `README.md` and `.env.example`.

### Acceptance criteria

- An authorized Access user can visit `/admin` without seeing a second password
  prompt and can use every existing admin workflow.
- Direct requests with no JWT, a forged JWT, the wrong issuer, or the wrong AUD
  receive `403` and cannot read exports or perform mutations.
- Public routes remain usable without a Cloudflare Access token.
- Logging out through the admin UI clears the Cloudflare Access session.
- Tests cover valid and invalid JWTs, signing-key rotation/cache refresh,
  `/admin` and nested `/admin/*` routes, CSRF enforcement, and public-route
  isolation.

### Deployment note

Keep the existing app password enabled during rollout. Deploy and verify Access
JWT enforcement first, including tests through the production Tunnel, and only
then remove the legacy password/session path.
