def test_homepage_cors_headers(app_client_with_cors):
    response = app_client_with_cors.get("/")
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert response.headers["Access-Control-Allow-Headers"] == "Authorization, Content-Type"
    assert response.headers["Access-Control-Expose-Headers"] == "Link"
    assert response.headers["Access-Control-Allow-Methods"] == "GET, POST, HEAD, OPTIONS"
