import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import click
from dotenv import load_dotenv
from flask import Flask, flash, redirect, url_for
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import generate_password_hash

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "local-development-key-change-me"),
        SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "sqlite:///tax_processing.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UPLOAD_FOLDER=os.getenv("UPLOAD_FOLDER", str(Path(app.instance_path) / "uploads")),
        MAX_CONTENT_LENGTH=int(os.getenv("MAX_CONTENT_LENGTH", 16 * 1024 * 1024)),
        DEBUG=os.getenv("FLASK_DEBUG", "false").lower() == "true",
        PUBLIC_BASE_URL=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
        ALLOWED_EXTENSIONS={"csv", "xlsx"},
    )
    public_host = urlparse(app.config["PUBLIC_BASE_URL"]).hostname
    app.config["TRUSTED_HOSTS"] = [public_host] if public_host else None
    app.config["PREFERRED_URL_SCHEME"] = "https"
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from tax_app import models
    from tax_app.models import DEFAULT_VALIDATION_RULES, ValidationRule
    from tax_app.routes import auth, main

    app.register_blueprint(auth.bp)
    app.register_blueprint(main.bp)

    @app.errorhandler(RequestEntityTooLarge)
    def handle_large_upload(_error):
        flash("The file exceeds the configured upload-size limit.", "danger")
        return redirect(url_for("main.upload"))

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(models.User, int(user_id))

    @app.cli.command("init-db")
    def init_db():
        """Create database tables and optionally provision an admin account."""
        db.create_all()
        for rule_key, label, category, severity in DEFAULT_VALIDATION_RULES:
            if not ValidationRule.query.filter_by(rule_key=rule_key).first():
                db.session.add(
                    ValidationRule(
                        rule_key=rule_key,
                        label=label,
                        category=category,
                        severity=severity,
                    )
                )
        db.session.commit()
        username = os.getenv("ADMIN_USERNAME")
        password = os.getenv("ADMIN_PASSWORD")
        if username and password and not models.User.query.filter_by(username=username).first():
            db.session.add(
                models.User(
                    username=username,
                    password_hash=generate_password_hash(password),
                )
            )
            db.session.commit()
            click.echo(f"Created administrator account '{username}'.")
        click.echo("Database initialized.")

    @app.cli.command("generate-sample")
    @click.option("--rows", default=10000, type=click.IntRange(min=10000))
    @click.option("--output", default="data/synthetic_transactions.csv")
    def generate_sample(rows, output):
        """Generate a deterministic, fictional financial transaction dataset."""
        from tax_app.sample_data import generate_sample_dataset

        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        generate_sample_dataset(destination, rows=rows)
        click.echo(f"Generated {rows:,} fictional records at {destination}.")

    return app
