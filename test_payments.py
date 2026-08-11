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


def test_pay_color_editor_has_synchronized_hex_control_and_bright_default():
    page = app.app.test_client().get("/buttn/edit/test").get_data(as_text=True)

    assert 'id="pay_button_color_input" type="color"' in page
    assert 'id="pay_button_hex_input" type="text" value="#00D900"' in page
    assert 'pattern="#[0-9A-Fa-f]{6}"' in page
    assert 'payButtonHexInput.value = payButtonColorInput.value.toUpperCase()' in page
    assert 'payButtonColorInput.value = normalized' in page
    assert 'renderLivePreview();' in page


def test_public_and_live_preview_use_larger_pay_button_styles():
    profile = dict(app.BUTTN_PROFILES["test"])
    profile["payment_links"] = [
        {"service": "venmo", "service_name": "", "url": "https://venmo.com/u/example"}
    ]
    app.BUTTN_PROFILES["payment-style-test"] = profile
    try:
        public_page = app.app.test_client().get("/buttn/payment-style-test").get_data(as_text=True)
        editor_page = app.app.test_client().get("/buttn/edit/test").get_data(as_text=True)

        for page in (public_page, editor_page):
            assert "padding:12px 20px" in page
            assert "font-size:17px" in page
            assert "white-space:nowrap" in page
    finally:
        app.BUTTN_PROFILES.pop("payment-style-test", None)
