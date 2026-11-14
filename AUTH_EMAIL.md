# Account verification and password recovery

Registration creates an unverified account and sends a verification email. Existing
accounts are backfilled as verified by an additive, idempotent migration. Existing
password hashes, profiles, plans, and subscriptions are preserved. New accounts
must verify before logging in. Verification does not change the existing trial or
subscription rules.

- `/resend-verification` requests another verification email.
- `/verify-email?token=...` displays a confirmation button; submitting activates
  the account. GET does not consume a link, protecting against email scanners.
- `/forgot-password` always returns the same confirmation for known, unknown,
  verified, unverified, throttled, and delivery-failure cases.
- `/reset-password?token=...` accepts and confirms a new password (8–1024
  characters). A successful reset invalidates **all** outstanding reset links
  for that user. It does not verify an unverified email address.

Tokens contain 256 bits of randomness. Only SHA-256 digests are stored. Verification
expires in 24 hours; password recovery expires in 30 minutes. Per-user row locks
and transactional rechecks prevent concurrent token reuse. Passwords use the
existing Werkzeug password hashing. New account forms use session CSRF tokens;
login also validates CSRF. Token pages disable caching and referrer disclosure.

## SES and Railway

No new Railway variables or AWS credentials are required. `boto3` uses the existing
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`, and
`SES_FROM_EMAIL`. Region/sender fallbacks are `us-west-2` and
`noreply@mybuttn.com`; no credentials are hardcoded. SES sends branded HTML and
plain-text alternatives with bounded network timeouts. Errors are logged without
provider details, email addresses, or tokens. Failed-send tokens are removed and
users can request another email.

Account links use the canonical origin `https://mybuttn.com` (never an incoming
Host header). Staging-generated emails therefore point to that domain too.
Use the application's existing production `SECRET_KEY` setting for sessions.
No changes are made to AWS, Railway, Stripe, or QR generation settings.

The migration runs through the existing `init_database()` mechanism and requires
its existing table-creation/alteration permissions. It adds only:

- `users.email_verified BOOLEAN NOT NULL DEFAULT TRUE` (registration explicitly
  inserts `FALSE`).
- `account_email_tokens` and its user/purpose index.
- `account_email_limits` for shared, atomic email-abuse counters.

Registration, resend, and reset requests share limits of five attempts per email
and twenty per network peer per UTC hour. Unknown addresses count too. The
network-peer limit deliberately uses `request.remote_addr`, not spoofable client
forwarding headers; a reverse proxy that shares a peer address also shares that
limit. Counters are shared across Railway workers and restarts. Stale counters
and expired tokens are removed during subsequent requests.

## Automated verification

Install requirements plus pytest, then run against a **disposable** PostgreSQL
database (the tests create and remove unique schemas, not production tables):

```sh
python -m pip install -r requirements.txt pytest
TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/TEST_DB python -m pytest -q
```

SES calls are mocked. Tests cover registration, blocked unverified login,
verification, resend, old-account login, password reset and hashing, expiry,
reuse, purpose mismatch, all-reset-link invalidation, concurrent consumption,
rate limiting, CSRF, delivery failures, multipart SES content, migration
idempotence, and preservation of profiles/subscriptions/passwords. Without
`TEST_DATABASE_URL`, database workflow tests explicitly skip.

## Live acceptance after review and deployment

No live emails or production migrations are performed by automated tests.
No deployment is performed as part of this implementation.

1. Register with a controlled mailbox on `mybuttn.com`; confirm SES delivery,
   sender identity, HTML/plain-text rendering, and mobile link opening.
2. Confirm login is blocked, resend verification, and activate using the email's
   link and confirmation button. Confirm the link cannot be reused.
3. Request password recovery for that account and an unknown address; check the
   identical on-screen confirmation. Complete a reset and verify new-password
   login, old-password rejection, and rejection of all earlier reset links.
4. Confirm a preexisting account can log in and retains its profile and plan.
5. Check delivered links resolve to the production HTTPS domain, the migration
   succeeds, and trusted users can send emails from separate connections. If
   Railway presents one shared proxy peer, review that deployment's trusted
   proxy behavior before tuning the peer limit; do not blindly trust arbitrary
   forwarding headers.

Expiry and concurrent-use behavior are covered without waiting in PostgreSQL
integration tests. SES delivery and Railway proxy behavior require the live checks
above with the already configured SES account.
