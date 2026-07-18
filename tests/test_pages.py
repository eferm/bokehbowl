def test_footer_names_operator(client):
    assert "Testy Operator" in client.get("/").text


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
