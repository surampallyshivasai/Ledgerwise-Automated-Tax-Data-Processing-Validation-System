import io
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd
from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, send_file, url_for,
)
from flask_login import current_user, login_required
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.utils import secure_filename

from tax_app import db
from tax_app.forms import UploadForm
from tax_app.models import (
    FinancialTransaction, GeneratedReport, ProcessingHistory, ReconciliationResult,
    UploadBatch, ValidationError, ValidationRule,
)
from tax_app.services.processing import process_upload, record_history

bp = Blueprint("main", __name__)
logger = logging.getLogger(__name__)


def _get_batch(batch_id):
    batch = db.session.get(UploadBatch, batch_id)
    if batch is None:
        abort(404)
    return batch


def _cleaned_rows(batch_id):
    return FinancialTransaction.query.filter_by(batch_id=batch_id).order_by(
        FinancialTransaction.row_number
    ).all()


def _amount(value):
    try:
        number = float(value)
        return number if pd.notna(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _spreadsheet_safe(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _spreadsheet_safe_frame(frame):
    return frame.map(_spreadsheet_safe)


def _analytics(batch):
    transactions = _cleaned_rows(batch.id)
    totals = {"revenue": 0.0, "expenses": 0.0, "tax_amount": 0.0}
    entity_amounts = defaultdict(float)
    category_amounts = Counter()
    monthly_amounts = Counter()
    for row in transactions:
        data = row.cleaned_data
        for field in totals:
            totals[field] += _amount(data.get(field))
        entity_amounts[data.get("entity_name") or "Unspecified"] += _amount(data.get("tax_amount"))
        category_amounts[data.get("tax_category") or "Unspecified"] += _amount(data.get("tax_amount"))
        date_value = data.get("transaction_date")
        if date_value:
            monthly_amounts[date_value[:7]] += _amount(data.get("revenue"))
    totals = {key: round(value, 2) for key, value in totals.items()}
    return {
        **totals,
        "taxable_income_estimate": round(totals["revenue"] - totals["expenses"], 2),
        "effective_tax_rate_estimate": (
            round(totals["tax_amount"] / totals["revenue"] * 100, 2)
            if totals["revenue"] else 0.0
        ),
        "entity_labels": list(entity_amounts),
        "entity_values": [round(value, 2) for value in entity_amounts.values()],
        "category_labels": list(category_amounts),
        "category_values": [round(value, 2) for value in category_amounts.values()],
        "month_labels": sorted(monthly_amounts),
        "month_values": [round(monthly_amounts[month], 2) for month in sorted(monthly_amounts)],
    }


@bp.route("/")
@login_required
def dashboard():
    latest = UploadBatch.query.order_by(UploadBatch.uploaded_at.desc()).first()
    batches = UploadBatch.query.order_by(UploadBatch.uploaded_at.desc()).limit(8).all()
    totals = {
        "records": sum(batch.total_records for batch in batches),
        "valid": sum(batch.valid_records for batch in batches),
        "invalid": sum(batch.invalid_records for batch in batches),
        "duplicates": sum(batch.duplicate_records for batch in batches),
        "missing": sum(batch.missing_values for batch in batches),
    }
    categories = Counter()
    if latest:
        categories.update({
            category: count for category, count in db.session.query(
                ValidationError.category, db.func.count(ValidationError.id)
            ).filter_by(batch_id=latest.id).group_by(ValidationError.category).all()
        })
    analytics = _analytics(latest) if latest and latest.total_records else None
    return render_template(
        "dashboard.html", latest=latest, batches=batches, totals=totals,
        categories=categories, analytics=analytics,
    )


@bp.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    form = UploadForm()
    if form.validate_on_submit():
        uploaded = form.file.data
        original_filename = secure_filename(uploaded.filename or "")
        extension = Path(original_filename).suffix.lower().lstrip(".")
        if not original_filename or extension not in current_app.config["ALLOWED_EXTENSIONS"]:
            flash("Choose a CSV or XLSX file.", "danger")
            return render_template("upload.html", form=form)
        stored_filename = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}_{original_filename}"
        stored_path = Path(current_app.config["UPLOAD_FOLDER"]) / stored_filename
        try:
            uploaded.save(stored_path)
            batch = UploadBatch(
                filename=original_filename,
                stored_path=str(stored_path),
                status="processing",
            )
            db.session.add(batch)
            db.session.commit()
            process_upload(batch, stored_path, extension)
            flash(f"Processed {batch.total_records:,} records from {original_filename}.", "success")
            return redirect(url_for("main.batch_detail", batch_id=batch.id))
        except ValueError as exc:
            db.session.rollback()
            batch = UploadBatch.query.filter_by(stored_path=str(stored_path)).first()
            if batch:
                batch.status = "failed"
                db.session.add(ProcessingHistory(
                    batch_id=batch.id, activity="Upload failed", details=str(exc)
                ))
                db.session.commit()
            logger.warning("Rejected upload %s: %s", original_filename, exc)
            flash(str(exc), "danger")
        except Exception:
            db.session.rollback()
            logger.exception("Unexpected error while processing uploaded file")
            failed_batch = UploadBatch.query.filter_by(stored_path=str(stored_path)).first()
            if failed_batch:
                failed_batch.status = "failed"
                db.session.add(ProcessingHistory(
                    batch_id=failed_batch.id,
                    activity="Upload failed",
                    details="Unexpected processing error; see application logs.",
                ))
                db.session.commit()
            flash("The upload could not be processed. Check the application logs for details.", "danger")
    return render_template("upload.html", form=form)


@bp.route("/batches/<int:batch_id>")
@login_required
def batch_detail(batch_id):
    batch = _get_batch(batch_id)
    reconciliation = ReconciliationResult.query.filter_by(batch_id=batch.id).order_by(
        ReconciliationResult.created_at.desc()
    ).first()
    return render_template("batch_detail.html", batch=batch, reconciliation=reconciliation)


@bp.route("/batches/<int:batch_id>/validation")
@login_required
def validation(batch_id):
    batch = _get_batch(batch_id)
    page = request.args.get("page", 1, type=int)
    errors = ValidationError.query.filter_by(batch_id=batch_id).order_by(
        ValidationError.id
    ).paginate(page=page, per_page=50, error_out=False)
    return render_template("validation.html", batch=batch, errors=errors)


@bp.route("/batches/<int:batch_id>/cleaning")
@login_required
def cleaning(batch_id):
    batch = _get_batch(batch_id)
    transactions = FinancialTransaction.query.filter_by(batch_id=batch_id).order_by(
        FinancialTransaction.row_number
    ).paginate(page=request.args.get("page", 1, type=int), per_page=50, error_out=False)
    return render_template("cleaning.html", batch=batch, transactions=transactions)


@bp.route("/batches/<int:batch_id>/cleaned.csv")
@login_required
def download_cleaned_data(batch_id):
    batch = _get_batch(batch_id)
    cleaned_rows = [
        {"source_row": row.row_number, "validation_status": "valid" if row.is_valid else "invalid",
         **row.cleaned_data}
        for row in _cleaned_rows(batch.id)
    ]
    columns = [
        "source_row", "validation_status", "transaction_id", "transaction_date",
        "entity_name", "department", "revenue", "expenses", "tax_category",
        "tax_amount", "currency", "transaction_status",
    ]
    output = _spreadsheet_safe_frame(pd.DataFrame(cleaned_rows, columns=columns)).to_csv(index=False)
    record_history(batch.id, "Cleaned dataset exported", f"{len(cleaned_rows)} normalized rows exported as CSV.")
    return send_file(
        io.BytesIO(output.encode("utf-8-sig")),
        as_attachment=True,
        download_name=f"cleaned-{batch.id}.csv",
        mimetype="text/csv",
    )


@bp.route("/batches/<int:batch_id>/reconciliation")
@login_required
def reconciliation(batch_id):
    batch = _get_batch(batch_id)
    result = ReconciliationResult.query.filter_by(batch_id=batch_id).order_by(
        ReconciliationResult.created_at.desc()
    ).first()
    return render_template("reconciliation.html", batch=batch, result=result)


@bp.route("/batches/<int:batch_id>/analytics")
@login_required
def analytics(batch_id):
    batch = _get_batch(batch_id)
    return render_template("analytics.html", batch=batch, analytics=_analytics(batch))


@bp.route("/reports")
@login_required
def reports():
    batches = UploadBatch.query.order_by(UploadBatch.uploaded_at.desc()).all()
    return render_template("reports.html", batches=batches)


@bp.route("/batches/<int:batch_id>/reports/<report_type>")
@login_required
def download_report(batch_id, report_type):
    batch = _get_batch(batch_id)
    if report_type not in {"xlsx", "csv", "pdf"}:
        abort(404)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"tax-processing-{batch.id}-{timestamp}.{report_type}"
    try:
        content = _build_report(batch, report_type)
        db.session.add(GeneratedReport(
            batch_id=batch.id, report_type=report_type, filename=filename
        ))
        db.session.add(ProcessingHistory(
            batch_id=batch.id, activity="Report generated",
            details=f"{report_type.upper()} report generated.",
        ))
        db.session.commit()
        return send_file(
            io.BytesIO(content), as_attachment=True, download_name=filename,
            mimetype={
                "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "csv": "text/csv",
                "pdf": "application/pdf",
            }[report_type],
        )
    except Exception:
        db.session.rollback()
        logger.exception("Report generation failed for batch %s (%s)", batch_id, report_type)
        flash("Report generation failed. Check application logs for details.", "danger")
        return redirect(url_for("main.batch_detail", batch_id=batch_id))


def _build_report(batch, report_type):
    transactions = _cleaned_rows(batch.id)
    errors = ValidationError.query.filter_by(batch_id=batch.id).order_by(ValidationError.id).all()
    reconciliation_result = ReconciliationResult.query.filter_by(batch_id=batch.id).first()
    error_rows = [
        {
            "record_id": error.record_id, "column": error.column_name,
            "original_value": error.original_value, "category": error.category,
            "description": error.description, "severity": error.severity,
            "suggestion": error.suggested_correction,
        }
        for error in errors
    ]
    transaction_rows = [
        {"row_number": row.row_number, "valid": row.is_valid, **row.cleaned_data}
        for row in transactions
    ]
    financial_metrics = _analytics(batch) if batch.total_records else {
        "revenue": 0, "expenses": 0, "tax_amount": 0,
        "taxable_income_estimate": 0, "effective_tax_rate_estimate": 0,
    }
    summary = pd.DataFrame([{
        "filename": batch.filename,
        "processing_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "records": batch.total_records,
        "valid_records": batch.valid_records,
        "invalid_records": batch.invalid_records,
        "duplicate_records": batch.duplicate_records,
        "missing_values": batch.missing_values,
        "total_revenue_unconverted": financial_metrics["revenue"],
        "total_expenses_unconverted": financial_metrics["expenses"],
        "recorded_tax_amount_unconverted": financial_metrics["tax_amount"],
        "illustrative_income_indicator": financial_metrics["taxable_income_estimate"],
        "recorded_tax_to_revenue_pct": financial_metrics["effective_tax_rate_estimate"],
        "validation_success_rate_pct": round(
            batch.valid_records / batch.total_records * 100, 2
        ) if batch.total_records else 0,
    }])
    if report_type == "csv":
        error_columns = [
            "record_id", "column", "original_value", "category",
            "description", "severity", "suggestion",
        ]
        exception_frame = _spreadsheet_safe_frame(pd.DataFrame(error_rows, columns=error_columns))
        return exception_frame.to_csv(index=False).encode("utf-8-sig")
    if report_type == "xlsx":
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            _spreadsheet_safe_frame(summary).to_excel(writer, sheet_name="Summary", index=False)
            _spreadsheet_safe_frame(pd.DataFrame(error_rows, columns=[
                "record_id", "column", "original_value", "category",
                "description", "severity", "suggestion",
            ])).to_excel(writer, sheet_name="Validation Errors", index=False)
            _spreadsheet_safe_frame(pd.DataFrame([reconciliation_result.summary | {
                "status": reconciliation_result.status,
                "revenue_difference": reconciliation_result.revenue_difference,
                "expense_difference": reconciliation_result.expense_difference,
                "mismatched_transaction_records": reconciliation_result.mismatched_transaction_count,
            }] if reconciliation_result else [])).to_excel(
                writer, sheet_name="Reconciliation", index=False
            )
            _spreadsheet_safe_frame(pd.DataFrame([{
                "total_revenue_unconverted": financial_metrics["revenue"],
                "total_expenses_unconverted": financial_metrics["expenses"],
                "recorded_tax_amount_unconverted": financial_metrics["tax_amount"],
                "illustrative_income_indicator": financial_metrics["taxable_income_estimate"],
                "recorded_tax_to_revenue_pct": financial_metrics["effective_tax_rate_estimate"],
            }])).to_excel(writer, sheet_name="Financial Summary", index=False)
            _spreadsheet_safe_frame(pd.DataFrame(transaction_rows)).to_excel(
                writer, sheet_name="Cleaned Data", index=False
            )
        return output.getvalue()

    output = io.BytesIO()
    document = SimpleDocTemplate(output, pagesize=letter)
    styles = getSampleStyleSheet()
    content = [
        Paragraph("Financial Data Processing Report", styles["Title"]),
        Paragraph(f"Source file: {escape(batch.filename)}", styles["Normal"]),
        Paragraph(f"Generated (UTC): {datetime.now(timezone.utc).isoformat()}", styles["Normal"]),
        Spacer(1, 12),
        Paragraph("Processing Summary", styles["Heading2"]),
    ]
    summary_rows = [
        ["Metric", "Value"],
        ["Total records", str(batch.total_records)],
        ["Valid records", str(batch.valid_records)],
        ["Invalid records", str(batch.invalid_records)],
        ["Duplicate records", str(batch.duplicate_records)],
        ["Missing values", str(batch.missing_values)],
        ["Revenue total (unconverted source units)", f"{financial_metrics['revenue']:,.2f}"],
        ["Expense total (unconverted source units)", f"{financial_metrics['expenses']:,.2f}"],
        ["Recorded tax amount (unconverted source units)", f"{financial_metrics['tax_amount']:,.2f}"],
        ["Illustrative income indicator", f"{financial_metrics['taxable_income_estimate']:,.2f}"],
    ]
    if reconciliation_result:
        summary_rows += [
            ["Reconciliation status", reconciliation_result.status.upper()],
            ["Mismatched transaction records", str(reconciliation_result.mismatched_transaction_count)],
            ["Revenue difference", f"${reconciliation_result.revenue_difference:,.2f}"],
            ["Expense difference", f"${reconciliation_result.expense_difference:,.2f}"],
        ]
    table = Table(summary_rows, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#12233f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
        ("PADDING", (0, 0), (-1, -1), 7),
    ]))
    content.append(table)
    content.extend([Spacer(1, 12), Paragraph(
        f"Validation exceptions recorded: {len(errors)}.", styles["Heading2"]
    )])
    exception_rows = [[
        Paragraph("<b>Record</b>", styles["BodyText"]),
        Paragraph("<b>Column</b>", styles["BodyText"]),
        Paragraph("<b>Severity</b>", styles["BodyText"]),
        Paragraph("<b>Exception</b>", styles["BodyText"]),
    ]]
    for error in errors[:25]:
        exception_rows.append([
            Paragraph(escape(str(error.record_id or "—")), styles["BodyText"]),
            Paragraph(escape(error.column_name), styles["BodyText"]),
            Paragraph(error.severity.title(), styles["BodyText"]),
            Paragraph(escape(error.description[:240]), styles["BodyText"]),
        ])
    if len(exception_rows) > 1:
        exception_table = Table(exception_rows, colWidths=[75, 85, 65, 285], repeatRows=1)
        exception_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#12233f")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.lightgrey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("PADDING", (0, 0), (-1, -1), 5),
        ]))
        content.append(exception_table)
        if len(errors) > 25:
            content.append(Paragraph(f"First 25 of {len(errors)} exceptions shown. See the Excel or CSV report for complete details.", styles["Normal"]))
    content.extend([
        Spacer(1, 12),
        Paragraph(
            "Amounts may combine currencies without conversion. Financial and tax indicators are illustrative only; this is not a tax return, tax opinion, or calculation of US tax liability.",
            styles["Italic"],
        ),
    ])
    document.build(content)
    return output.getvalue()


@bp.route("/history")
@login_required
def history():
    batches = UploadBatch.query.order_by(UploadBatch.uploaded_at.desc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=25, error_out=False
    )
    activities = ProcessingHistory.query.order_by(ProcessingHistory.created_at.desc()).limit(50).all()
    return render_template("history.html", batches=batches, activities=activities)


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    rules = ValidationRule.query.order_by(ValidationRule.id).all()
    if request.method == "POST":
        for rule in rules:
            rule.enabled = request.form.get(f"rule_{rule.id}") == "on"
        db.session.commit()
        flash("Validation rule settings saved. Changes apply to future uploads.", "success")
        return redirect(url_for("main.settings"))
    return render_template("settings.html", rules=rules)


@bp.route("/api/batches/<int:batch_id>/analytics")
@login_required
def analytics_api(batch_id):
    batch = _get_batch(batch_id)
    return jsonify(_analytics(batch))
