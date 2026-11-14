"""Run with TEST_DATABASE_URL pointing to a disposable PostgreSQL database.
SES is mocked; these tests never send email or access production data.
"""
import os
import re
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash, check_password_hash

import app as buttn
import auth_email


@pytest.fixture
def auth(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable PostgreSQL database")
    engine = create_engine(url)
    # A unique schema isolates every test and never drops existing application tables.
    import secrets
    schema = "auth_test_" + secrets.token_hex(8)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    with scoped.begin() as conn:
        conn.execute(text("""CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, account_type TEXT NOT NULL DEFAULT 'trial',
            trial_started_at TIMESTAMPTZ DEFAULT NOW(), trial_ends_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW())"""))
        conn.execute(text("INSERT INTO users(email,password_hash,account_type) VALUES ('legacy@example.com',:pw,'pro')"),
                     {"pw": generate_password_hash("legacy-password")})
    monkeypatch.setattr(buttn, "engine", scoped)
    monkeypatch.setattr(buttn, "_db_initialized", False)
    monkeypatch.setattr(buttn, "verify_turnstile_response", lambda *args: True)
    monkeypatch.setitem(buttn.app.config, "TESTING", True)
    ses = Mock()
    monkeypatch.setattr(auth_email.boto3, "client", Mock(return_value=ses))
    buttn.init_database()
    client = buttn.app.test_client()
    yield client, scoped, ses
    scoped.dispose()
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


def post(client, path, **data):
    client.get(path)
    with client.session_transaction() as state:
        data["csrf_token"] = state["email_csrf"]
    return client.post(path, data=data)


def register(client):
    return post(client, "/register", email="new@example.com", password="original-password")


def link(ses):
    body = ses.send_email.call_args.kwargs["Message"]["Body"]["Text"]["Data"]
    return re.search(r"https://mybuttn.com(/\S+)", body).group(1)


def login(client, email="new@example.com", password="original-password"):
    return post(client, "/login", email=email, password=password)


def test_registration_verification_and_login(auth):
    client, engine, ses = auth
    assert b"Account created" in register(client).data
    with client.session_transaction() as state:
        assert "user_id" not in state
    assert b"verify your email" in login(client).data
    url = link(ses)
    assert "verify-email" in url
    with engine.connect() as conn:
        row = conn.execute(text("SELECT token_hash, expires_at FROM account_email_tokens")).one()
        assert url.split("token=")[1] not in row.token_hash
        assert 86390 < row.expires_at - auth_email.time.time() <= 86400
    assert b"Confirm below" in client.get(url).data
    assert b"verify your email" in login(client).data
    assert b"Your account is active" in post(client, url).data
    assert login(client).status_code == 302
    assert post(client, url).status_code == 400


def test_reset_new_password_and_all_links_invalidated(auth):
    client, engine, ses = auth
    for _ in range(2):
        post(client, "/forgot-password", email="legacy@example.com")
        if _ == 0:
            first = link(ses)
    second = link(ses)
    with engine.connect() as conn:
        expires = conn.execute(text("SELECT expires_at FROM account_email_tokens LIMIT 1")).scalar_one()
        assert 1790 < expires - auth_email.time.time() <= 1800
    response = post(client, first, password="new-password", confirm_password="new-password")
    assert b"password has been updated" in response.data
    assert login(client, "legacy@example.com", "legacy-password").status_code == 200
    assert login(client, "legacy@example.com", "new-password").status_code == 302
    assert client.get(first).status_code == 400
    assert client.get(second).status_code == 400
    with engine.connect() as conn:
        pw = conn.execute(text("SELECT password_hash FROM users WHERE email='legacy@example.com'")).scalar_one()
        assert pw != "new-password" and check_password_hash(pw, "new-password")


@pytest.mark.parametrize("purpose,path,email", [("verify", "/resend-verification", "new@example.com"), ("reset", "/forgot-password", "legacy@example.com")])
def test_expired_invalid_and_wrong_purpose(auth, purpose, path, email):
    client, engine, ses = auth
    if purpose == "verify":
        register(client)
    post(client, path, email=email)
    url = link(ses)
    wrong = url.replace("verify-email", "reset-password") if purpose == "verify" else url.replace("reset-password", "verify-email")
    assert client.get(wrong).status_code == 400
    with engine.begin() as conn:
        conn.execute(text("UPDATE account_email_tokens SET expires_at=0"))
    assert client.get(url).status_code == 400
    assert client.get(url.split("token=")[0] + "token=invalid").status_code == 400


def test_existing_accounts_and_migration_idempotence(auth):
    client, engine, ses = auth
    buttn._db_initialized = False
    buttn.init_database()
    assert login(client, "legacy@example.com", "legacy-password").status_code == 302
    with engine.connect() as conn:
        user = conn.execute(text("SELECT email_verified, account_type FROM users")).one()
        assert user.email_verified and user.account_type == "pro"


def test_requests_do_not_reveal_account_existence(auth):
    client, engine, ses = auth
    for path in ("/forgot-password", "/resend-verification"):
        known = post(client, path, email="legacy@example.com")
        missing = post(client, path, email="missing@example.com")
        assert known.status_code == missing.status_code == 200
        assert known.data == missing.data


def test_resend_rate_limits_and_delivery_failure(auth):
    client, engine, ses = auth
    register(client)
    ses.send_email.reset_mock()
    for _ in range(8):
        post(client, "/resend-verification", email="new@example.com")
    assert ses.send_email.call_count == 4  # Registration already used one allowance.
    ses.send_email.side_effect = RuntimeError("sensitive provider error")
    response = post(client, "/forgot-password", email="legacy@example.com")
    assert auth_email.GENERIC.encode() in response.data
    assert b"sensitive" not in response.data
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM account_email_tokens WHERE purpose='reset'")).scalar_one() == 0


def test_registration_delivery_failure_allows_retry(auth):
    client, engine, ses = auth
    ses.send_email.side_effect = RuntimeError("delivery failed")
    assert b"Account created, but" in register(client).data
    ses.send_email.side_effect = None
    post(client, "/resend-verification", email="new@example.com")
    assert b"Your account is active" in post(client, link(ses)).data


def test_csrf_password_validation_and_security_headers(auth):
    client, engine, ses = auth
    assert client.post("/forgot-password", data={"email": "legacy@example.com"}).status_code == 400
    post(client, "/forgot-password", email="legacy@example.com")
    url = link(ses)
    response = client.get(url)
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert client.post(url, data={"password": "new-password"}).status_code == 400
    assert post(client, url, password="short", confirm_password="short").status_code == 400
    assert post(client, url, password="new-password", confirm_password="different").status_code == 400
    assert client.get(url).status_code == 200


def test_ses_settings_and_multipart_content(auth, monkeypatch):
    client, engine, ses = auth
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    monkeypatch.setenv("SES_FROM_EMAIL", "noreply@mybuttn.com")
    post(client, "/forgot-password", email="legacy@example.com")
    assert auth_email.boto3.client.call_args.args == ("ses",)
    assert auth_email.boto3.client.call_args.kwargs["region_name"] == "us-west-2"
    kwargs = ses.send_email.call_args.kwargs
    assert kwargs["Source"] == "noreply@mybuttn.com"
    assert set(kwargs["Message"]["Body"]) == {"Html", "Text"}
    assert "BUTTN" in kwargs["Message"]["Body"]["Html"]["Data"]


def test_concurrent_reset_consumed_once(auth):
    client, engine, ses = auth
    post(client, "/forgot-password", email="legacy@example.com")
    token = link(ses).split("token=")[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: auth_email.consume(engine, token, "reset", "new-password"), range(2)))
    assert sorted(results) == [False, True]


def test_registration_ip_limit_across_different_emails(auth):
    client, engine, ses = auth
    with buttn.app.test_request_context("/register"):
        for i in range(20):
            assert auth_email.allowed(engine, f"person{i}@example.com")
        assert not auth_email.allowed(engine, "another@example.com")


def test_email_migration_preserves_profiles_subscriptions_and_passwords(auth):
    client, engine, ses = auth
    with engine.begin() as conn:
        user = conn.execute(text("SELECT id, password_hash FROM users WHERE email='legacy@example.com'")).one()
        conn.execute(text("INSERT INTO profiles(user_id,username,name) VALUES (:id,'legacy-profile','Existing profile')"), {"id": user.id})
        conn.execute(text("""INSERT INTO buttn_stripe_subscriptions
            (subscription_id,user_id,customer_id,livemode,status,grants_pro)
            VALUES ('sub_existing',:id,'cus_existing',TRUE,'active',TRUE)"""), {"id": user.id})
        before_user = dict(conn.execute(text("SELECT * FROM users WHERE id=:id"), {"id": user.id}).mappings().one())
        before_profile = dict(conn.execute(text("SELECT * FROM profiles WHERE user_id=:id"), {"id": user.id}).mappings().one())
        before_sub = dict(conn.execute(text("SELECT * FROM buttn_stripe_subscriptions WHERE user_id=:id"), {"id": user.id}).mappings().one())
        # Simulate a legacy installation, then run precisely the additive email migration twice.
        conn.execute(text("ALTER TABLE users DROP COLUMN email_verified"))
        auth_email.migrate(conn)
        auth_email.migrate(conn)
        assert dict(conn.execute(text("SELECT * FROM users WHERE id=:id"), {"id": user.id}).mappings().one()) == before_user
        assert dict(conn.execute(text("SELECT * FROM profiles WHERE user_id=:id"), {"id": user.id}).mappings().one()) == before_profile
        assert dict(conn.execute(text("SELECT * FROM buttn_stripe_subscriptions WHERE user_id=:id"), {"id": user.id}).mappings().one()) == before_sub


def test_rate_limit_resets_in_next_hour(auth, monkeypatch):
    client, engine, ses = auth
    clock = int(auth_email.time.time())
    monkeypatch.setattr(auth_email.time, "time", lambda: clock)
    with buttn.app.test_request_context("/forgot-password"):
        for _ in range(5):
            assert auth_email.allowed(engine, "legacy@example.com")
        assert not auth_email.allowed(engine, "legacy@example.com")
        clock += 3600
        assert auth_email.allowed(engine, "legacy@example.com")


def test_login_csrf_and_unicode_csrf_rejected(auth):
    client, engine, ses = auth
    client.get("/login")
    assert client.post("/login", data={"email": "legacy@example.com", "password": "legacy-password"}).status_code == 400
    assert client.post("/forgot-password", data={"email": "legacy@example.com", "csrf_token": "é"}).status_code == 400
    ses.send_email.assert_not_called()


def test_password_reset_does_not_verify_unverified_account(auth):
    client, engine, ses = auth
    register(client)
    post(client, "/forgot-password", email="new@example.com")
    url = link(ses)
    assert b"password has been updated" in post(client, url, password="replacement-password", confirm_password="replacement-password").data
    assert b"verify your email" in login(client, password="replacement-password").data


def test_resend_does_not_email_already_verified_accounts(auth):
    client, engine, ses = auth
    assert auth_email.GENERIC.encode() in post(client, "/resend-verification", email="legacy@example.com").data
    ses.send_email.assert_not_called()
