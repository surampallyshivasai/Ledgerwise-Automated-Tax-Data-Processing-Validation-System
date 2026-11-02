from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user
from werkzeug.security import check_password_hash
from urllib.parse import urlparse

from tax_app.forms import LoginForm
from tax_app.models import User

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data.strip()).first()
        if user and check_password_hash(user.password_hash, form.password.data):
            login_user(user)
            next_url = request.args.get("next")
            if next_url and urlparse(next_url).scheme == "" and urlparse(next_url).netloc == "":
                return redirect(next_url)
            return redirect(url_for("main.dashboard"))
        flash("The username or password was not recognized.", "danger")
    return render_template("login.html", form=form)


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
