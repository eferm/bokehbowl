def test_footer_names_operator(client):
    assert "Testy Operator" in client.get("/").text


def test_favicon_served(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert "svg" in response.headers["content-type"]


def test_footer_shows_running_commit(client):
    page = client.get("/").text
    assert "abc1234" in page
    assert "abc1234def" not in page


def test_about_page(client):
    page = client.get("/about")
    assert page.status_code == 200
    assert "Testy Operator" in page.text
    assert "operator@example.com" in page.text


def test_privacy_page(client):
    page = client.get("/privacy")
    assert page.status_code == 200
    assert "Testy Operator" in page.text
    assert "never sold" in page.text
