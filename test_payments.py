import app


def test_payment_url_validation_accepts_only_safe_http_urls():
    assert app._safe_payment_url("https://example.com/pay/me") == "https://example.com/pay/me"
    assert app._safe_payment_url("http://example.com/pay") == "http://example.com/pay"
    assert app._safe_payment_url("javascript:alert(1)") == ""
    assert app._safe_payment_url("mailto:owner@example.com") == ""
    assert app._safe_payment_url("https://user:secret@example.com/pay") == ""
    assert app._safe_payment_url("example.com/pay") == ""


def test_public_profile_single_payment_link_opens_directly():
    profile = dict(app.BUTTN_PROFILES["test"])
    profile["payment_links"] = [{"service": "venmo", "service_name": "", "url": "https://venmo.com/u/example"}]
    app.BUTTN_PROFILES["payment-test-single"] = profile
    try:
        response = app.app.test_client().get("/buttn/payment-test-single")
        page = response.get_data(as_text=True)
        assert response.status_code == 200
        assert '$&nbsp; Pay' in page
        assert 'href="https://venmo.com/u/example"' in page
        assert 'id="payment-choice-modal"' not in page
    finally:
        app.BUTTN_PROFILES.pop("payment-test-single", None)


def test_public_profile_multiple_links_shows_only_configured_choices():
    profile = dict(app.BUTTN_PROFILES["test"])
    profile["payment_links"] = [
        {"service": "cash_app", "service_name": "", "url": "https://cash.app/$example"},
        {"service": "other", "service_name": "Ko-fi", "url": "https://ko-fi.com/example"},
    ]
    app.BUTTN_PROFILES["payment-test-multiple"] = profile
    try:
        page = app.app.test_client().get("/buttn/payment-test-multiple").get_data(as_text=True)
        assert 'id="payment-choice-modal"' in page
        assert "Choose how you'd like to pay" in page
        assert "Cash App" in page
        assert "Ko-fi" in page
        assert "Venmo</a>" not in page
    finally:
        app.BUTTN_PROFILES.pop("payment-test-multiple", None)
