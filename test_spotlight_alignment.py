import app


def test_public_profile_centers_only_spotlight_copy():
    profile = dict(app.BUTTN_PROFILES["test"])
    profile.update(
        {
            "spotlight_enabled": True,
            "spotlight_headline": "New Drop Available",
            "spotlight_subtext": "Tap to learn more.",
            "spotlight_url": "https://example.com/featured",
        }
    )
    app.BUTTN_PROFILES["spotlight-alignment-test"] = profile

    try:
        response = app.app.test_client().get("/buttn/spotlight-alignment-test")
        page = response.get_data(as_text=True)

        assert response.status_code == 200
        assert ".spotlight-card { display:block; width:100%; text-align:left;" in page
        assert ".spotlight-copy { padding:16px; text-align:center; }" in page
        assert '<div class="spotlight-kicker">Featured</div>' in page
        assert "<h2>New Drop Available</h2>" in page
        assert '<div class="spotlight-subtext">Tap to learn more.</div>' in page
    finally:
        app.BUTTN_PROFILES.pop("spotlight-alignment-test", None)
