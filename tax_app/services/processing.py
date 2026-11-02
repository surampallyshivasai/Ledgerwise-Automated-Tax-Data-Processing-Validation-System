import logging
import re
from collections import Counter
from datetime import datetime

import pandas as pd

from tax_app import db
from tax_app.models import (
    FinancialTransaction,
    ProcessingHistory,
    ReconciliationResult,
    UploadBatch,
    ValidationError,
    ValidationRule,
)

logger = logging.getLogger(__name__)

FIELD_ALIASES = {
    "transaction_id": "transaction_id",
    "transaction_date": "transaction_date",
    "entity_name": "entity_name",
    "department": "department",
    "revenue": "revenue",
    "expenses": "expenses",
    "tax_category": "tax_category",
    "tax_amount": "tax_amount",
    "currency": "currency",
    "transaction_status": "transaction_status",
    "status": "transaction_status",
}
REQUIRED_FIELDS = {
    "transaction_id",
    "transaction_date",
    "entity_name",
    "tax_category",
    "currency",
    "transaction_status",
}
NUMERIC_FIELDS = {"revenue", "expenses", "tax_amount"}
VALID_CATEGORIES = {
    "ordinary income",
    "cost of services",
    "depreciation",
    "interest income",
    "other expense",
}
VALID_CURRENCIES = {
    "USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "CNY", "INR", "MXN",
    "BRL", "NZD", "SGD", "HKD", "SEK", "NOK", "DKK", "KRW", "ZAR", "AED",
}
VALID_STATUSES = {"posted", "pending", "reconciled", "void", "reversed"}


def _is_missing(value):
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _safe_value(value):
    if _is_missing(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def _standardize_columns(frame):
    rename = {}
    seen = set()
    for column in frame.columns:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(column).strip().lower()).strip("_")
        canonical = FIELD_ALIASES.get(normalized, normalized)
        if canonical in seen:
            raise ValueError(f"Multiple columns map to '{canonical}'. Please use unique headers.")
        rename[column] = canonical
        seen.add(canonical)
    return frame.rename(columns=rename)


def _clean_record(record):
    cleaned = {}
    for key, value in record.items():
        value = _safe_value(value)
        if isinstance(value, str):
            value = value.strip()
            if not value:
                value = None
        if key in NUMERIC_FIELDS:
            if value is None:
                cleaned[key] = None
            else:
                parsed = pd.to_numeric(value, errors="coerce")
                cleaned[key] = float(parsed) if not pd.isna(parsed) else None
        elif key == "transaction_date" and value is not None:
            parsed_date = pd.to_datetime(value, errors="coerce", utc=True)
            cleaned[key] = None if pd.isna(parsed_date) else parsed_date.strftime("%Y-%m-%d")
        elif key in {"currency", "transaction_status"} and value is not None:
            cleaned[key] = str(value).upper()
        else:
            cleaned[key] = value
    return cleaned


def _add_error(errors, record, batch_id, column, category, description, severity, suggestion):
    errors.append(
        ValidationError(
            batch_id=batch_id,
            transaction_row_id=record["db_id"],
            record_id=str(record["cleaned"].get("transaction_id") or ""),
            column_name=column,
            original_value=str(record["original"].get(column) if record["original"].get(column) is not None else "")[:2000],
            category=category,
            description=description,
            severity=severity,
            suggested_correction=suggestion,
        )
    )


def validate_records(records, batch_id):
    errors = []
    id_counts = {}
    for record in records:
        transaction_id = record["cleaned"].get("transaction_id")
        if transaction_id:
            id_counts[transaction_id] = id_counts.get(transaction_id, 0) + 1

    for record in records:
        cleaned = record["cleaned"]
        original = record["original"]
        for field in REQUIRED_FIELDS:
            if not cleaned.get(field):
                _add_error(errors, record, batch_id, field, "missing_value",
                           f"Required field '{field}' is missing.", "high",
                           "Provide a value from the source accounting records.")

        transaction_id = cleaned.get("transaction_id")
        if transaction_id and id_counts.get(transaction_id, 0) > 1:
            _add_error(errors, record, batch_id, "transaction_id", "duplicate",
                       f"Transaction ID '{transaction_id}' occurs {id_counts[transaction_id]} times.",
                       "high", "Assign a unique transaction ID or remove the duplicate in a reviewed copy.")

        raw_date = original.get("transaction_date")
        if cleaned.get("transaction_date") is None and raw_date is not None:
            _add_error(errors, record, batch_id, "transaction_date", "invalid_date",
                       "Transaction date cannot be parsed as a valid date.", "high",
                       "Correct the date using an unambiguous date format such as YYYY-MM-DD.")

        for field in NUMERIC_FIELDS:
            raw_value = original.get(field)
            clean_value = cleaned.get(field)
            if raw_value is not None and clean_value is None:
                _add_error(errors, record, batch_id, field, "incorrect_data_type",
                           f"'{field}' must be a numeric amount.", "high",
                           "Replace the value with a number; verify the source ledger.")
            elif clean_value is not None and clean_value < 0:
                _add_error(errors, record, batch_id, field, "invalid_amount",
                           "Financial amounts cannot be negative in this transaction schema.", "high",
                           "Verify sign convention and transaction type against the source.")

        raw_currency = cleaned.get("currency")
        if raw_currency and raw_currency not in VALID_CURRENCIES:
            _add_error(errors, record, batch_id, "currency", "invalid_currency",
                       f"'{raw_currency}' is not an accepted ISO 4217 currency code.", "medium",
                       "Use a valid three-letter currency code.")

        category = cleaned.get("tax_category")
        if category and str(category).strip().lower() not in VALID_CATEGORIES:
            _add_error(errors, record, batch_id, "tax_category", "invalid_tax_category",
                       f"'{category}' is not in the configured tax category list.", "medium",
                       "Map the transaction to an approved tax category; consult a tax professional.")

        status = cleaned.get("transaction_status")
        if status and status.lower() not in VALID_STATUSES:
            _add_error(errors, record, batch_id, "transaction_status", "invalid_status",
                       f"'{status}' is not an accepted transaction status.", "medium",
                       "Use Posted, Pending, Reconciled, Void, or Reversed.")

        entity = cleaned.get("entity_name")
        if entity and (len(str(entity)) > 200 or any(ord(char) < 32 for char in str(entity))):
            _add_error(errors, record, batch_id, "entity_name", "invalid_entity",
                       "Entity name contains unsupported characters or exceeds 200 characters.",
                       "medium", "Provide a clean legal entity name from the entity master.")

        revenue = cleaned.get("revenue")
        expenses = cleaned.get("expenses")
        tax_amount = cleaned.get("tax_amount")
        if revenue is not None and expenses is not None and revenue == 0 and expenses == 0:
            _add_error(errors, record, batch_id, "revenue", "financial_inconsistency",
                       "Both revenue and expenses are zero; confirm this is an intentional transaction.",
                       "low", "Review the source transaction and populate applicable amounts.")
        if revenue is not None and tax_amount is not None and tax_amount > revenue and revenue > 0:
            _add_error(errors, record, batch_id, "tax_amount", "financial_inconsistency",
                       "Tax amount exceeds recorded revenue; this may indicate a data mapping issue.",
                       "medium", "Verify tax amount and revenue against source records.")
    disabled_categories = {
        category
        for (category,) in db.session.query(ValidationRule.category).filter_by(enabled=False).all()
    }
    return [error for error in errors if error.category not in disabled_categories]


def _load_frame(file_path, extension):
    try:
        if extension == "csv":
            return pd.read_csv(file_path, dtype=object, keep_default_na=True)
        return pd.read_excel(file_path, dtype=object, engine="openpyxl")
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError, OSError) as exc:
        raise ValueError(f"Unable to read the uploaded file: {exc}") from exc


def process_upload(batch, file_path, extension):
    """Load, preserve, clean, validate, and reconcile a single uploaded file."""
    frame = _load_frame(file_path, extension)
    if frame.empty:
        raise ValueError("The uploaded file contains no data rows.")
    if len(frame) > 100000:
        raise ValueError("Files are limited to 100,000 rows per upload.")
    frame = _standardize_columns(frame)
    missing_columns = sorted(REQUIRED_FIELDS - set(frame.columns))
    if missing_columns:
        raise ValueError("Missing required columns: " + ", ".join(missing_columns))

    records = []
    transactions = []
    for row_number, raw_row in enumerate(frame.to_dict(orient="records"), start=2):
        original = {key: _safe_value(value) for key, value in raw_row.items()}
        cleaned = _clean_record(original)
        transaction = FinancialTransaction(
            batch_id=batch.id,
            row_number=row_number,
            transaction_id=str(cleaned.get("transaction_id") or "") or None,
            original_data=original,
            cleaned_data=cleaned,
            is_valid=True,
        )
        transactions.append(transaction)
        records.append({"original": original, "cleaned": cleaned, "transaction": transaction})
    db.session.add_all(transactions)
    db.session.flush()
    records_with_ids = [
        {"original": item["original"], "cleaned": item["cleaned"], "db_id": item["transaction"].id}
        for item in records
    ]
    validation_errors = validate_records(records_with_ids, batch.id)
    errors_by_transaction = {}
    for error in validation_errors:
        errors_by_transaction.setdefault(error.transaction_row_id, []).append(error)
    for transaction in transactions:
        transaction.is_valid = transaction.id not in errors_by_transaction
    db.session.add_all(validation_errors)
    batch.total_records = len(transactions)
    batch.invalid_records = len(errors_by_transaction)
    batch.valid_records = batch.total_records - batch.invalid_records
    id_frequencies = Counter(
        row["cleaned"].get("transaction_id") for row in records
        if row["cleaned"].get("transaction_id")
    )
    batch.duplicate_records = sum(
        1 for row in records
        if row["cleaned"].get("transaction_id")
        and id_frequencies[row["cleaned"]["transaction_id"]] > 1
    )
    batch.missing_values = sum(
        1 for row in records for value in row["original"].values() if _is_missing(value)
    )

    reconciliation = _reconcile(batch, records)
    db.session.add(reconciliation)
    db.session.add(
        ProcessingHistory(
            batch_id=batch.id,
            activity="Upload processed",
            details=f"{batch.total_records} records; {batch.valid_records} valid; {batch.invalid_records} invalid.",
        )
    )
    batch.status = "completed"
    db.session.commit()
    logger.info("Processed batch %s with %s rows and %s validation errors",
                batch.id, batch.total_records, len(validation_errors))
    return batch


def _reconcile(batch, records):
    def amount_total(key, original=False):
        total = 0.0
        for row in records:
            value = row["original"].get(key) if original else row["cleaned"].get(key)
            parsed = pd.to_numeric(value, errors="coerce")
            if not pd.isna(parsed):
                total += float(parsed)
        return round(total, 2)

    source_revenue = amount_total("revenue", original=True)
    processed_revenue = amount_total("revenue")
    source_expenses = amount_total("expenses", original=True)
    processed_expenses = amount_total("expenses")
    missing_ids = sum(not row["cleaned"].get("transaction_id") for row in records)
    transaction_ids = [
        row["cleaned"].get("transaction_id") for row in records
        if row["cleaned"].get("transaction_id")
    ]
    id_frequencies = Counter(transaction_ids)
    mismatched_records = missing_ids + sum(
        1 for transaction_id in transaction_ids if id_frequencies[transaction_id] > 1
    )
    revenue_difference = round(source_revenue - processed_revenue, 2)
    expense_difference = round(source_expenses - processed_expenses, 2)
    status = (
        "matched"
        if abs(revenue_difference) < 0.01
        and abs(expense_difference) < 0.01
        and mismatched_records == 0
        else "review"
    )
    return ReconciliationResult(
        batch_id=batch.id,
        source_revenue=source_revenue,
        processed_revenue=processed_revenue,
        revenue_difference=revenue_difference,
        source_expenses=source_expenses,
        processed_expenses=processed_expenses,
        expense_difference=expense_difference,
        missing_transaction_count=missing_ids,
        mismatched_transaction_count=mismatched_records,
        status=status,
        summary={
            "transaction_rows": len(records),
            "missing_transaction_ids": missing_ids,
            "mismatched_transaction_records": mismatched_records,
            "method": "Raw numeric totals compared with normalized numeric totals; no records removed.",
        },
    )


def record_history(batch_id, activity, details=""):
    db.session.add(ProcessingHistory(batch_id=batch_id, activity=activity, details=details))
    db.session.commit()
