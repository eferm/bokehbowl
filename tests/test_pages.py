def test_home_page_uses_gallery_design(client):
    page = client.get("/").text
    assert 'class="photo-theme home"' in page
    assert "Photographs are better on paper." in page
    assert "Free pictures, mailed occasionally." in page


def test_site_stylesheet_served(client):
    response = client.get("/static/site.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_placeholder_artwork_served(client):
    for path, content_type in [
        ("/static/background.webp", "image/webp"),
        ("/static/og.jpg", "image/jpeg"),
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"] == content_type


def test_instance_static_shadows_default(make_client, monkeypatch, tmp_path):
    instance_static = tmp_path / "static"
    instance_static.mkdir()
    (instance_static / "background.webp").write_bytes(b"operator image bytes")
    monkeypatch.setattr("bokehbowl.app.INSTANCE_STATIC_DIR", instance_static)
    with make_client() as client:
        response = client.get("/static/background.webp")
        assert response.status_code == 200
        assert response.content == b"operator image bytes"
        assert client.get("/static/site.css").status_code == 200


def test_favicon_served(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert "svg" in response.headers["content-type"]
    assert 'href="/favicon.ico"' in client.get("/").text


def test_footer_shows_running_commit(client):
    page = client.get("/").text
    assert "abc1234" in page
    assert "abc1234def" not in page


def test_security_headers(client):
    headers = client.get("/").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Content-Security-Policy"] == "frame-ancestors 'none'"


def test_privacy_page(client):
    page = client.get("/privacy")
    assert page.status_code == 200
    assert 'class="prose"' in page.text
    assert "Testy Operator" in page.text
    assert "never sold" in page.text


def test_goodbye_page_uses_prose_layout(client):
    page = client.get("/goodbye")
    assert page.status_code == 200
    assert 'class="prose"' in page.text


def test_signup_rejects_multipart(client):
    response = client.post(
        "/signup", data={"email": "a@example.com"}, files={"f": ("x.bin", b"xx")}
    )
    assert response.status_code == 415


def test_signup_rejects_oversized_body(client):
    response = client.post("/signup", data={"email": "a" * 70000})
    assert response.status_code == 413


def test_signup_rejects_chunked_body(client):
    response = client.post(
        "/signup",
        content=iter([b"email=a%40example.com&" + b"padding=" + b"x" * 70_000]),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 411
