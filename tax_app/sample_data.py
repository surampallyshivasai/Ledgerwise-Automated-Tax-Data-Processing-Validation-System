"""Reproducible fictional financial data generator."""

from pathlib import Path

import numpy as np
import pandas as pd

ENTITIES = [
    "Northstar Analytics LLC",
    "Juniper Cloud Systems Inc",
    "Cedar Ridge Manufacturing LLC",
    "Blue Harbor Services Inc",
    "Summit Trail Logistics LLC",
]
DEPARTMENTS = ["Sales", "Engineering", "Operations", "Finance", "Research"]
CATEGORIES = ["Ordinary Income", "Cost of Services", "Depreciation", "Interest Income", "Other Expense"]
CURRENCIES = ["USD", "USD", "USD", "CAD", "EUR"]


def generate_sample_dataset(output_path, rows=10000, seed=2026):
    """Write a synthetic dataset with controlled data-quality exceptions."""
    rng = np.random.default_rng(seed)
    row_numbers = np.arange(1, rows + 1)
    revenue = np.round(rng.uniform(0, 250000, rows), 2)
    expenses = np.round(rng.uniform(0, 180000, rows), 2)
    tax_amount = np.round(revenue * rng.uniform(0, 0.25, rows), 2)
    dates = pd.Timestamp("2025-01-01") + pd.to_timedelta(
        rng.integers(0, 365, rows), unit="D"
    )
    frame = pd.DataFrame(
        {
            "Transaction ID": [f"TX-{number:07d}" for number in row_numbers],
            "Transaction Date": dates.strftime("%Y-%m-%d"),
            "Entity Name": rng.choice(ENTITIES, rows),
            "Department": rng.choice(DEPARTMENTS, rows),
            "Revenue": revenue,
            "Expenses": expenses,
            "Tax Category": rng.choice(CATEGORIES, rows),
            "Tax Amount": tax_amount,
            "Currency": rng.choice(CURRENCIES, rows),
            "Transaction Status": rng.choice(["Posted", "Pending", "Reconciled"], rows),
        }
    )
    frame["Revenue"] = frame["Revenue"].astype(object)

    # Introduce deterministic, visible exceptions without changing the saved source.
    for index in range(0, rows, 997):
        frame.loc[index, "Transaction ID"] = frame.loc[max(index - 1, 0), "Transaction ID"]
    for index in range(41, rows, 613):
        frame.loc[index, "Entity Name"] = np.nan
    for index in range(109, rows, 887):
        frame.loc[index, "Transaction Date"] = "not-a-date"
    for index in range(173, rows, 719):
        frame.loc[index, "Expenses"] = -abs(frame.loc[index, "Expenses"])
    for index in range(257, rows, 991):
        frame.loc[index, "Currency"] = "USX"
    for index in range(331, rows, 1091):
        frame.loc[index, "Tax Category"] = "Unclassified"
    for index in range(419, rows, 1201):
        frame.loc[index, "Revenue"] = np.nan
    for index in range(503, rows, 1301):
        frame.loc[index, "Tax Amount"] = -abs(frame.loc[index, "Tax Amount"])
    for index in range(587, rows, 1409):
        frame.loc[index, "Transaction Status"] = "Unknown"
    for index in range(677, rows, 1523):
        frame.loc[index, "Revenue"] = "not-a-number"

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return frame
