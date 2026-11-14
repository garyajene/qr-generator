"""BUTTN account email workflows. Tokens are never stored in plaintext."""
import hashlib
import hmac
import html
import os
import secrets
import time

import boto3
from botocore.config import Config
from flask import request, session
from sqlalchemy import text
from werkzeug.security import generate_password_hash

PUBLIC_ORIGIN = "https://mybuttn.com"
GENERIC = "If this email is eligible, a link will arrive shortly. Check your inbox and spam folder. You can try again later if needed."
INVALID = "This link is invalid, expired, or already used. Please request a new link."


def migrate(conn):
    # The TRUE default backfills all existing users; registration explicitly uses FALSE.
    conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT TRUE"))
    conn.execute(text("""CREATE TABLE IF NOT EXISTS account_email_tokens (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        purpose TEXT NOT NULL CHECK (purpose IN ('verify', 'reset')),
        expires_at BIGINT NOT NULL
    )"""))
    conn.execute(text("CREATE INDEX IF NOT EXISTS account_email_tokens_user ON account_email_tokens (user_id, purpose)"))
    conn.execute(text("""CREATE TABLE IF NOT EXISTS account_email_limits (
        key_hash TEXT PRIMARY KEY, bucket BIGINT NOT NULL, attempts INTEGER NOT NULL
    )"""))


def csrf_field():
    if "email_csrf" not in session:
        session["email_csrf"] = secrets.token_urlsafe(32)
    return '<input type="hidden" name="csrf_token" value="' + html.escape(session["email_csrf"], quote=True) + '">'


def valid_csrf():
    return bool(session.get("email_csrf")) and hmac.compare_digest(session["email_csrf"].encode(), request.form.get("csrf_token", "").encode())


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def allowed(engine, email):
    """Shared across workers; do not trust client-supplied forwarding headers."""
    now = int(time.time()) // 3600
    with engine.begin() as conn:
        # Retain only current/recent buckets to bound storage.
        conn.execute(text("DELETE FROM account_email_limits WHERE bucket < :old"), {"old": now - 1})
        for scope, value, limit in (("ip", request.remote_addr or "unknown", 20), ("email", email, 5)):
            count = conn.execute(text("""INSERT INTO account_email_limits (key_hash, bucket, attempts)
                VALUES (:key, :bucket, 1)
                ON CONFLICT (key_hash) DO UPDATE SET
                attempts = CASE WHEN account_email_limits.bucket = :bucket
                    THEN account_email_limits.attempts + 1 ELSE 1 END,
                bucket = :bucket RETURNING attempts"""),
                {"key": _digest(scope + ":" + value), "bucket": now}).scalar_one()
            if count > limit:
                return False
    return True


def _lock_user(conn, user_id):
    suffix = " FOR UPDATE" if conn.dialect.name == "postgresql" else ""
    return conn.execute(text("SELECT id, email, email_verified FROM users WHERE id = :id" + suffix), {"id": user_id}).mappings().first()


def send_email(email, purpose, token):
    verify = purpose == "verify"
    title = "Verify your email" if verify else "Reset your password"
    path = "/verify-email" if verify else "/reset-password"
    link = PUBLIC_ORIGIN + path + "?token=" + token
    expiry = "24 hours" if verify else "30 minutes"
    intro = "Welcome to BUTTN! Confirm your email to activate your account." if verify else "We received a request to reset your BUTTN password."
    plain = f"{title}\n\n{intro}\n\n{link}\n\nThis single-use link expires in {expiry}. If you didn't request this, you can ignore this email.\n\nThe BUTTN team | mybuttn.com"
    body = f'''<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;background:#f3f5f7;font-family:Arial,sans-serif;color:#111;padding:20px">
<div style="max-width:520px;margin:auto;background:white;padding:28px;border-radius:18px">
<img src="{PUBLIC_ORIGIN}/static/buttn_logo_blk.png" alt="BUTTN" width="142" height="45" style="display:block;width:142px;max-width:100%;height:auto;margin-bottom:24px;border:0"><h1 style="font-size:24px">{title}</h1>
<p style="line-height:1.6">{intro}</p><p style="margin:30px 0"><a href="{link}" style="display:inline-block;background:#111;color:white;padding:16px 24px;border-radius:12px;text-decoration:none;font-weight:bold">{title}</a></p>
<p style="line-height:1.6">This single-use link expires in {expiry}. If you didn’t request this, you can ignore this email.</p>
<p style="font-size:13px;color:#666">Button not working? Copy this link:</p><p style="word-break:break-all;font-size:13px"><a href="{link}">{link}</a></p>
<hr style="border:0;border-top:1px solid #dde1e7"><p style="color:#666;font-size:13px">The BUTTN team · mybuttn.com</p></div></body></html>'''
    # boto3's default credential chain reads the existing Railway AWS environment.
    boto3.client("ses", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-west-2"),
                 config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 1})).send_email(
        Source=os.environ.get("SES_FROM_EMAIL", "noreply@mybuttn.com"),
        Destination={"ToAddresses": [email]},
        Message={"Subject": {"Data": title + " | BUTTN", "Charset": "UTF-8"},
                 "Body": {"Text": {"Data": plain, "Charset": "UTF-8"},
                          "Html": {"Data": body, "Charset": "UTF-8"}}})


def send_for_user(engine, user, purpose):
    token = secrets.token_urlsafe(32)
    digest = _digest(token)
    try:
        with engine.begin() as conn:
            current = _lock_user(conn, user["id"])
            if not current or (purpose == "verify" and current["email_verified"]):
                return False
            conn.execute(text("DELETE FROM account_email_tokens WHERE expires_at <= :now"), {"now": int(time.time())})
            conn.execute(text("""INSERT INTO account_email_tokens (token_hash, user_id, purpose, expires_at)
                VALUES (:hash, :id, :purpose, :expires)"""),
                {"hash": digest, "id": user["id"], "purpose": purpose,
                 "expires": int(time.time()) + (86400 if purpose == "verify" else 1800)})
        send_email(user["email"], purpose, token)
        return True
    except Exception:
        # No tokens, email addresses, credentials, or provider errors in logs/responses.
        from flask import current_app
        current_app.logger.warning("Account email could not be delivered (%s)", purpose)
        try:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM account_email_tokens WHERE token_hash = :hash"), {"hash": digest})
        except Exception:
            pass
        return False


def _find(conn, token, purpose):
    if not token or len(token) > 128:
        return None
    return conn.execute(text("""SELECT user_id FROM account_email_tokens
        WHERE token_hash = :hash AND purpose = :purpose AND expires_at > :now"""),
        {"hash": _digest(token), "purpose": purpose, "now": int(time.time())}).scalar()


def consume(engine, token, purpose, password=None):
    with engine.begin() as conn:
        user_id = _find(conn, token, purpose)
        if user_id is None:
            return False
        _lock_user(conn, user_id)
        # Recheck after locking: competing requests cannot consume the same token.
        if _find(conn, token, purpose) is None:
            return False
        if purpose == "verify":
            conn.execute(text("UPDATE users SET email_verified = TRUE WHERE id = :id"), {"id": user_id})
        else:
            conn.execute(text("UPDATE users SET password_hash = :password WHERE id = :id"),
                         {"id": user_id, "password": generate_password_hash(password)})
        conn.execute(text("DELETE FROM account_email_tokens WHERE user_id = :id AND purpose = :purpose"),
                     {"id": user_id, "purpose": purpose})
    return True


def install(app, get_engine, init_database, page):
    @app.after_request
    def private_email_pages(response):
        if request.path in ("/verify-email", "/reset-password", "/forgot-password", "/resend-verification"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def request_link(purpose):
        title = "Resend verification email" if purpose == "verify" else "Forgot Password?"
        fields = '<label for="account_email">Email</label><input id="account_email" type="email" name="email" required autocomplete="email"><button type="submit">Send email</button>'
        if request.method == "POST":
            if not valid_csrf():
                return page(title, "Please reload the page and try again.", form_html=fields), 400
            email = (request.form.get("email") or "").strip().lower()
            try:
                init_database()
                engine = get_engine()
                if engine is not None and email and len(email) <= 254 and allowed(engine, email):
                    with engine.connect() as conn:
                        user = conn.execute(text("SELECT id, email, email_verified FROM users WHERE email = :email"), {"email": email}).mappings().first()
                    if user and (purpose == "reset" or not user["email_verified"]):
                        send_for_user(engine, user, purpose)
            except Exception:
                app.logger.warning("Account email request unavailable")
            return page(title, GENERIC, form_html=fields)
        return page(title, form_html=fields)

    app.add_url_rule("/forgot-password", "forgot_password", lambda: request_link("reset"), methods=["GET", "POST"])
    app.add_url_rule("/resend-verification", "resend_verification", lambda: request_link("verify"), methods=["GET", "POST"])

    @app.route("/verify-email", methods=["GET", "POST"])
    def verify_email():
        # GET is deliberately non-mutating so email security scanners cannot consume links.
        token = request.args.get("token", "")
        fields = '<button type="submit">Verify email</button>'
        try:
            init_database()
            if request.method == "POST":
                if not valid_csrf():
                    return page("Verify your email", "Please reload the page and try again.", form_html=fields), 400
                if consume(get_engine(), token, "verify"):
                    return page("Email verified", "Your account is active. You can now log in.", form_html="")
            else:
                with get_engine().connect() as conn:
                    if _find(conn, token, "verify") is not None:
                        return page("Verify your email", "Confirm below to activate your BUTTN account.", form_html=fields)
        except Exception:
            app.logger.warning("Email verification unavailable")
        return page("Verify your email", INVALID, form_html=""), 400

    @app.route("/reset-password", methods=["GET", "POST"])
    def reset_password():
        token = request.args.get("token", "")
        fields = '<p>Use at least 8 characters.</p><label for="new_password">New password</label><input id="new_password" type="password" name="password" minlength="8" maxlength="1024" required autocomplete="new-password"><label for="confirm_password">Confirm password</label><input id="confirm_password" type="password" name="confirm_password" minlength="8" maxlength="1024" required autocomplete="new-password"><label class="show-pass-row"><input id="show_password_toggle" type="checkbox" aria-controls="new_password confirm_password"> Show Password</label><button type="submit">Reset password</button>'
        try:
            init_database()
            with get_engine().connect() as conn:
                exists = _find(conn, token, "reset") is not None
            if not exists:
                return page("Reset password", INVALID, form_html=""), 400
            if request.method == "POST":
                if not valid_csrf():
                    return page("Reset password", "Please reload the page and try again.", form_html=fields), 400
                password = request.form.get("password", "")
                if len(password) < 8 or len(password) > 1024 or password != request.form.get("confirm_password"):
                    return page("Reset password", "Use 8–1024 characters and make sure both passwords match.", form_html=fields), 400
                if consume(get_engine(), token, "reset", password):
                    session.clear()
                    return page("Password reset", "Your password has been updated. Log in with your new password.", form_html="")
                return page("Reset password", INVALID, form_html=""), 400
            return page("Reset password", form_html=fields)
        except Exception:
            app.logger.warning("Password reset unavailable")
            return page("Reset password", "We could not process this request. Please try again later.", form_html=""), 503
