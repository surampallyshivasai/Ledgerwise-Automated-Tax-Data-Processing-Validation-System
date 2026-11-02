import pytest
from werkzeug.security import generate_password_hash

from tax_app import create_app, db
from tax_app.models import User


@pytest.fixture
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-key-not-for-production",
            "PUBLIC_BASE_URL": "",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "MAX_CONTENT_LENGTH": 16 * 1024 * 1024,
        }
    )
    with app.app_context():
        db.create_all()
        db.session.add(
            User(username="tester", password_hash=generate_password_hash("test-password"))
        )
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in_client(client):
    response = client.post(
        "/login",
        data={"username": "tester", "password": "test-password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    return client
