import io

from openpyxl import load_workbook

from tax_app import db
from tax_app.models import (
    FinancialTransaction, ReconciliationResult, UploadBatch, ValidationError, ValidationRule,
)


CSV_CONTENT = (
    "Transaction ID,Transaction Date,Entity Name,Department,Revenue,Expenses,"
    "Tax Category,Tax Amount,Currency,Transaction Status\n"
    "TX-1,2025-01-01,Example LLC,Sales,100.00,25.00,Ordinary Income,10.00,USD,Posted\n"
    "TX-2,2025-02-01,Example LLC,Ops,120.00,20.00,Interest Income,12.00,USD,Reconciled\n"
    "TX-2,not-a-date,,Ops,-10.00,0.00,Unknown,-5.00,USX,Unknown\n"
)


def upload_csv(client, content=CSV_CONTENT, filename="transactions.csv"):
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(content.encode("utf-8")), filename)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_upload_preserves_original_and_creates_cleaned_validated_copy(app, logged_in_client):
    response = upload_csv(logged_in_client)
    assert response.status_code == 200
    assert b"transactions.csv" in response.data
    with app.app_context():
        batch = UploadBatch.query.one()
        assert batch.status == "completed"
        assert (batch.total_records, batch.valid_records, batch.invalid_records) == (3, 1, 2)
        assert batch.duplicate_records == 2
        with open(batch.stored_path, "r", encoding="utf-8") as original_file:
            assert original_file.read() == CSV_CONTENT
        rows = FinancialTransaction.query.filter_by(batch_id=batch.id).order_by(
            FinancialTransaction.row_number
        ).all()
        assert rows[0].original_data["revenue"] == "100.00"
        assert rows[0].cleaned_data["revenue"] == 100.0
        assert rows[2].cleaned_data["transaction_date"] is None
        categories = {error.category for error in ValidationError.query.filter_by(batch_id=batch.id)}
        assert {"duplicate", "invalid_date", "missing_value", "invalid_amount",
                "invalid_currency", "invalid_tax_category"} <= categories


def test_reconciliation_compares_raw_and_normalized_totals(app, logged_in_client):
    upload_csv(logged_in_client)
    with app.app_context():
        result = ReconciliationResult.query.one()
        assert result.source_revenue == 210.0
        assert result.processed_revenue == 210.0
        assert result.revenue_difference == 0
        assert result.source_expenses == 45.0
        assert result.processed_expenses == 45.0
        assert result.status == "review"
        assert result.mismatched_transaction_count == 2


def test_pages_and_exports_work_end_to_end(app, logged_in_client):
    upload_csv(logged_in_client)
    with app.app_context():
        batch_id = UploadBatch.query.one().id
    for path in [
        "/", "/upload", "/history", "/reports", "/settings",
        f"/batches/{batch_id}", f"/batches/{batch_id}/cleaning", f"/batches/{batch_id}/validation",
        f"/batches/{batch_id}/reconciliation", f"/batches/{batch_id}/analytics",
    ]:
        assert logged_in_client.get(path).status_code == 200
    cleaned_csv = logged_in_client.get(f"/batches/{batch_id}/cleaned.csv")
    assert cleaned_csv.status_code == 200
    assert b"validation_status,transaction_id" in cleaned_csv.data

    csv_report = logged_in_client.get(f"/batches/{batch_id}/reports/csv")
    assert csv_report.status_code == 200
    assert b"invalid_date" in csv_report.data
    xlsx_report = logged_in_client.get(f"/batches/{batch_id}/reports/xlsx")
    assert xlsx_report.status_code == 200
    assert xlsx_report.data.startswith(b"PK")
    workbook = load_workbook(io.BytesIO(xlsx_report.data), read_only=True)
    assert {"Summary", "Validation Errors", "Reconciliation", "Financial Summary", "Cleaned Data"} <= set(workbook.sheetnames)
    workbook.close()
    pdf_report = logged_in_client.get(f"/batches/{batch_id}/reports/pdf")
    assert pdf_report.status_code == 200
    assert pdf_report.data.startswith(b"%PDF")


def test_upload_rejects_missing_required_columns(logged_in_client):
    response = upload_csv(
        logged_in_client,
        "Transaction ID,Transaction Date\nTX-1,2025-01-01\n",
        "incomplete.csv",
    )
    assert response.status_code == 200
    assert b"Missing required columns" in response.data


def test_login_is_required(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_page_has_branded_favicon_and_optional_custom_canonical(app, client):
    app.config["PUBLIC_BASE_URL"] = "https://tax.example.test"
    response = client.get("/login")
    assert response.status_code == 200
    assert b'rel="canonical" href="https://tax.example.test/login"' in response.data
    assert b'rel="icon" type="image/svg+xml"' in response.data
    assert b"SECURE WORKSPACE" not in response.data


def test_settings_change_validation_rules_for_future_uploads(app, logged_in_client):
    with app.app_context():
        db.session.add(ValidationRule(
            rule_key="duplicate", label="Duplicate IDs", category="duplicate",
            severity="high", enabled=True,
        ))
        db.session.commit()
        rule_id = ValidationRule.query.one().id
    response = logged_in_client.post("/settings", data={"csrf_token": ""}, follow_redirects=True)
    assert response.status_code == 200
    upload_csv(logged_in_client)
    with app.app_context():
        assert db.session.get(ValidationRule, rule_id).enabled is False
        assert UploadBatch.query.one().duplicate_records == 2
        assert ValidationError.query.filter_by(category="duplicate").count() == 0


def test_spreadsheet_exports_escape_formula_like_values(app, logged_in_client):
    content = CSV_CONTENT.replace("TX-1,", "=2+2,")
    upload_csv(logged_in_client, content)
    with app.app_context():
        batch_id = UploadBatch.query.one().id
    report = logged_in_client.get(f"/batches/{batch_id}/reports/xlsx")
    workbook = load_workbook(io.BytesIO(report.data), data_only=False, read_only=True)
    values = list(workbook["Cleaned Data"].values)
    transaction_id_index = list(values[0]).index("transaction_id")
    assert values[1][transaction_id_index] == "'=2+2"
    workbook.close()
