# Optional OpenID Connect login

OIDC adds identity-provider sign-in to the existing MuckScraper accounts and
Reader, Scraper and Admin roles. It does not change the news pipeline.
It is disabled by default. Existing password login remains available unless
explicitly disabled.

## Configure a provider

Register a confidential web client with your provider. Enable authorization
code flow and PKCE S256. Register this exact callback, using your own origin:

```text
https://reader.example.com/auth/oidc/callback
```

Configure the application through environment variables or your deployment's
secret injection mechanism. Never commit client secrets.

```dotenv
OIDC_ENABLED=true
OIDC_ISSUER_URL=https://identity.example.com/realms/readers
OIDC_CLIENT_ID=muckscraper
OIDC_CLIENT_SECRET=inject-a-client-secret
OIDC_REDIRECT_URI=https://reader.example.com/auth/oidc/callback
# Optional, requires registration with the provider:
OIDC_POST_LOGOUT_REDIRECT_URI=https://reader.example.com/auth/oidc/logout/callback
OIDC_PROVIDER_NAME=Organization sign-in
OIDC_SCOPES=openid profile email groups
OIDC_ALLOWED_GROUPS=["news-readers"]
OIDC_ROLE_MAPPING={"news-operators":"scraper","news-admins":"admin"}
OIDC_CREATE_USERS=false
OIDC_ALLOW_PASSWORD_LOGIN=true
OIDC_AUTO_LOGIN=false
OIDC_SESSION_LIFETIME_SECONDS=3600
```

Apply database migrations before enabling OIDC. The identity migration only
creates a new table; it does not reset users, change passwords, or copy news data.
The issuer and callback must use HTTPS. The callback is explicit rather than
derived from untrusted forwarded headers. Configure any reverse-proxy trust
separately and register the same external URL at the provider.

The provider must return a signed ID token with a verified email address.
Group restrictions and role mappings use a string array in the signed token;
the claim name defaults to `groups`, configurable with `OIDC_GROUPS_CLAIM`.
Claims available only from a separate UserInfo endpoint are not used.

## Accounts and linking

Identity is the exact issuer and subject pair. Email and username are not
identity keys, and matching emails never automatically link existing accounts.
Email and username collisions refuse account creation rather than overwriting an
account. A newly created account receives its verified provider email. For a
linked account, the email saved in MuckScraper's profile remains unchanged at
sign-in.

Automatic account creation is off. An administrator can explicitly bind an
independently verified provider subject to an existing active account:

```sh
flask --app aggregator.app link-oidc-identity --username existing-reader --subject verified-subject
flask --app aggregator.app link-oidc-identity --username existing-reader --subject verified-subject --apply
```

The first command is a dry-run. The second creates one identity binding.
Repeated application to the same account is harmless; replacing a binding with
another account is refused. The command never looks up a subject by email and
does not change the account's password or role.

Set `OIDC_CREATE_USERS=true` to allow verified identities to create accounts.
Generated usernames do not use the provider's display name. New accounts have
no usable local password and start as Readers unless explicit role mapping
grants a higher role. Disabled local accounts cannot be reactivated by OIDC.

## Access and roles

`OIDC_ALLOWED_GROUPS=[]` accepts any otherwise valid identity from the configured
issuer. A nonempty list requires membership of at least one named group.
With `OIDC_ROLE_MAPPING={}`, existing roles remain locally managed.

A nonempty role mapping is authoritative at each successful OIDC sign-in. The
highest matching role wins; an identity with no mapped role becomes a Reader.
This also applies to explicitly linked local accounts. Admin mappings grant
full administrative access, so use narrowly controlled groups. A change that
would demote the final active administrator refuses sign-in and leaves the
account unchanged. Retain and test an administrator recovery route before
disabling local login.

`OIDC_ALLOW_PASSWORD_LOGIN=false` removes the password form and refuses password
sign-in POSTs. It does not invalidate sessions issued before the setting changed;
plan a session-key rotation separately if existing local sessions must be revoked.
`PUBLIC_READ_ACCESS` remains an independent, explicit setting.

Set `OIDC_AUTO_LOGIN=true` to send signed-out visitors from the login page
directly to the configured provider. The default is false. Public reading pages
remain public when enabled separately. The original local return path is
preserved through the existing OIDC flow.

Use `/auth/login?manual=1` to show the sign-in page without an automatic redirect.
This does not enable password login if it is disabled. After sign-out or a failed
or cancelled provider attempt, automatic login pauses for that browser session.
The provider sign-in button remains available for an explicit retry. A successful
sign-in clears the pause. The option initiates the normal provider login flow;
it does not bypass provider authentication or consent.

## Sessions, profile and logout

Authlib validates the signed ID token, issuer, audience, expiry and nonce.
Authorization requests use browser-bound, single-use state and PKCE. Only one
pending OIDC sign-in is retained per browser session; a new start removes its
old Authlib state before creating another attempt. Invalid or superseded callbacks
are refused without consuming the current attempt.
Return URLs are restricted to local paths. Authentication responses are not
cached. OIDC responses do not send referrers; the password page allows same-origin
referrers so HTTPS form submissions retain CSRF protection. Errors expose only an exception class, never provider
responses, tokens or user attributes.

No access, refresh or ID token is stored in the identity table or logged. The
application session records the identity binding and an absolute expiry. Expiry
is enforced server-side on subsequent requests; it does not slide with activity.
The default is one hour, configurable from 60 seconds to 24 hours. Provider group
changes take effect at sign-in, not immediately in existing sessions.

During an OIDC session, the profile explains that identity details are managed
at the provider and refuses local email/password changes. Sign-out requires a
CSRF-protected POST. It clears the local session and any remember-me cookie,
unless optional RP-initiated logout is configured. Set
`OIDC_POST_LOGOUT_REDIRECT_URI` and register that exact callback with the
provider to request sign-out at its discovered HTTPS end-session endpoint.
If the endpoint is unavailable, local sign-out still works. Providers can ask
for confirmation or require an ID token hint, which this implementation does
not retain. Back-channel logout is not implemented.

## Verification

```sh
PYTHONPATH=. pytest -q tests/test_oidc.py
```

Authentication tests exercise the real Authlib verifier with signed fixture
tokens, SQL identity bindings, Flask sessions and CSRF. Provider HTTP boundaries
are mocked and outbound socket access is refused. Tests cover both the default
password flow and enabled OIDC, including invalid signatures/claims, state replay,
account collisions, explicit linking, role mapping, disabled users and expiry.

Deployment acceptance must also exercise real provider login and logout in a
fresh browser. On iPhone, test Safari and a separately installed home-screen web
app, including the return from the provider. A manifest or passing unit test is
not evidence that this browser flow has passed. This feature does not create a
native App Store client.
