from flask_wtf import FlaskForm
from wtforms import FileField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Sign in")


class UploadForm(FlaskForm):
    file = FileField("Financial data file", validators=[DataRequired()])
    submit = SubmitField("Upload and process")
