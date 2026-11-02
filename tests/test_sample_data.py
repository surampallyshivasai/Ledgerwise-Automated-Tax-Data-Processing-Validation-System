import pandas as pd

from tax_app.sample_data import generate_sample_dataset
from tax_app.models import ReconciliationResult, UploadBatch


def test_generator_creates_large_fictional_dataset_with_controlled_errors(tmp_path):
    destination = tmp_path / "sample.csv"
    frame = generate_sample_dataset(destination, rows=10000, seed=7)
    assert len(frame) == 10000
    assert len(pd.read_csv(destination)) == 10000
    assert frame["Transaction ID"].duplicated().any()
    assert (frame["Transaction Date"] == "not-a-date").any()
    assert (frame["Expenses"] < 0).any()
    assert frame["Entity Name"].isna().any()
    assert frame["Currency"].eq("USX").any()
    assert destination.exists()


def test_large_dataset_upload_is_processed_and_reconciled(app, logged_in_client, tmp_path):
    destination = tmp_path / "large_sample.csv"
    generate_sample_dataset(destination, rows=10000, seed=10)
    with destination.open("rb") as source:
        response = logged_in_client.post(
            "/upload",
            data={"file": (source, "large_sample.csv")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    assert response.status_code == 200
    with app.app_context():
        batch = UploadBatch.query.one()
        result = ReconciliationResult.query.one()
        assert batch.total_records == 10000
        assert batch.invalid_records > 0
        assert batch.duplicate_records > 0
        assert result.revenue_difference == 0
        assert result.expense_difference == 0
