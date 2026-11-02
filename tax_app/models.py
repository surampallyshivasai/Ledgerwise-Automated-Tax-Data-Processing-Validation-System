from datetime import datetime, timezone

from flask_login import UserMixin

from tax_app import db

DEFAULT_VALIDATION_RULES = [
    ("missing_value", "Required fields", "Missing mandatory fields", "high"),
    ("duplicate", "Duplicate IDs", "Duplicate transaction IDs", "high"),
    ("invalid_date", "Date format", "Invalid transaction dates", "high"),
    ("invalid_amount", "Financial amount signs", "Negative financial amounts", "high"),
    ("incorrect_data_type", "Numeric data types", "Non-numeric financial values", "high"),
    ("invalid_currency", "Currency codes", "Unaccepted currency codes", "medium"),
    ("invalid_tax_category", "Tax categories", "Unconfigured tax categories", "medium"),
    ("invalid_status", "Transaction status", "Unaccepted transaction statuses", "medium"),
    ("invalid_entity", "Entity format", "Invalid entity names", "medium"),
    ("financial_inconsistency", "Financial consistency", "Suspicious financial relationships", "medium"),
]


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class UploadBatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    stored_path = db.Column(db.String(500), nullable=False)
    uploaded_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False, default="processing")
    total_records = db.Column(db.Integer, nullable=False, default=0)
    valid_records = db.Column(db.Integer, nullable=False, default=0)
    invalid_records = db.Column(db.Integer, nullable=False, default=0)
    duplicate_records = db.Column(db.Integer, nullable=False, default=0)
    missing_values = db.Column(db.Integer, nullable=False, default=0)
    transactions = db.relationship("FinancialTransaction", backref="batch", cascade="all, delete-orphan")
    errors = db.relationship("ValidationError", backref="batch", cascade="all, delete-orphan")


class FinancialTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("upload_batch.id"), nullable=False, index=True)
    row_number = db.Column(db.Integer, nullable=False)
    transaction_id = db.Column(db.String(120), index=True)
    original_data = db.Column(db.JSON, nullable=False)
    cleaned_data = db.Column(db.JSON, nullable=False)
    is_valid = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class ValidationRule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rule_key = db.Column(db.String(80), unique=True, nullable=False)
    label = db.Column(db.String(160), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    severity = db.Column(db.String(20), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)


class ValidationError(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("upload_batch.id"), nullable=False, index=True)
    transaction_row_id = db.Column(db.Integer, db.ForeignKey("financial_transaction.id"), nullable=False, index=True)
    record_id = db.Column(db.String(120))
    column_name = db.Column(db.String(120), nullable=False)
    original_value = db.Column(db.Text)
    category = db.Column(db.String(80), nullable=False, index=True)
    description = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(20), nullable=False)
    suggested_correction = db.Column(db.Text)
    transaction = db.relationship("FinancialTransaction", backref="validation_errors")


class ReconciliationResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("upload_batch.id"), nullable=False, index=True)
    source_revenue = db.Column(db.Float, nullable=False, default=0)
    processed_revenue = db.Column(db.Float, nullable=False, default=0)
    revenue_difference = db.Column(db.Float, nullable=False, default=0)
    source_expenses = db.Column(db.Float, nullable=False, default=0)
    processed_expenses = db.Column(db.Float, nullable=False, default=0)
    expense_difference = db.Column(db.Float, nullable=False, default=0)
    missing_transaction_count = db.Column(db.Integer, nullable=False, default=0)
    mismatched_transaction_count = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(30), nullable=False)
    summary = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class ProcessingHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("upload_batch.id"), nullable=False, index=True)
    activity = db.Column(db.String(120), nullable=False)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class GeneratedReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("upload_batch.id"), nullable=False, index=True)
    report_type = db.Column(db.String(30), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
