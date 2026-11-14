"""Account branding checks; SES is mocked and no production services are used."""
from unittest.mock import Mock

import pytest
import app
import auth_email


@pytest.mark.parametrize("path", ["/register", "/login", "/verify-email", "/forgot-password", "/reset-password", "/resend-verification"])
def test_account_pages_use_official_light_background_logo(monkeypatch, path):
    monkeypatch.setattr(app, "engine", None)
    response = app.app.test_client().get(path)
    page = response.get_data(as_text=True)
    assert 'src="/static/buttn_logo_blk.png"' in page
    assert 'class="auth-brand-logo"' in page
    assert 'alt="BUTTN"' in page
    assert 'buttn_logo_wht.png' not in page
    logo = app.app.test_client().get('/static/buttn_logo_blk.png')
    assert logo.status_code == 200 and logo.mimetype == 'image/png'


@pytest.mark.parametrize("purpose", ["verify", "reset"])
def test_emails_use_official_logo_with_absolute_https_url(monkeypatch, purpose):
    ses = Mock()
    monkeypatch.setattr(auth_email.boto3, "client", Mock(return_value=ses))
    auth_email.send_email("test@example.com", purpose, "test-token")
    message = ses.send_email.call_args.kwargs["Message"]
    body = message["Body"]["Html"]["Data"]
    assert 'src="https://mybuttn.com/static/buttn_logo_blk.png"' in body
    assert 'alt="BUTTN"' in body and 'width="142"' in body
    assert '<p style="font-size:28px;font-weight:bold">BUTTN</p>' not in body
    assert "The BUTTN team" in message["Body"]["Text"]["Data"]
    assert "test-token" in body and "test-token" in message["Body"]["Text"]["Data"]
