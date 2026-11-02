<div align="center">
  <img src="tax_app/static/favicon.svg" width="64" height="64" alt="Ledgerwise ledger icon">

  <h1>Ledgerwise</h1>
  <p><strong>Financial data processing, validation &amp; reconciliation</strong></p>

  <p>A practical operations workspace for reviewing financial transaction files,<br>
  finding data-quality exceptions, and comparing source totals with processed records.</p>

  <br>

  <a href="#screenshots">Screenshots</a> ·
  <a href="#what-it-does">Features</a> ·
  <a href="#run-locally">Setup</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#important-notes">Limitations</a>
</div>

<br>

> **A note on tax calculations**
> Ledgerwise reports descriptive totals and simplified financial indicators. It does not apply tax rates, interpret tax law, prepare returns, or calculate US tax liability. Treat all outputs as operational review aids, not tax advice.

---

## At a glance

| | |
|:---|:---|
| **Built with** | Python · Flask · Pandas · SQLAlchemy · Bootstrap · Chart.js |
| **Data store** | SQLite by default; MySQL available through SQLAlchemy |
| **Inputs** | CSV and Excel `.xlsx` |
| **Outputs** | Excel workbook, CSV exceptions, PDF summary |
| **Sample data** | Fictional, reproducible transaction data in [`data/synthetic_transactions.csv`](data/synthetic_transactions.csv) |

## Screenshots

<p align="center">
  <img src="screenshots/dashboard.png" width="90%" alt="Ledgerwise dashboard showing processing quality and financial trends">
</p>

<details>
  <summary>View the rest of the application</summary>

- [Sign in](screenshots/login.png)
- [Upload](screenshots/upload.png)
- [Processing](screenshots/processing.png)
- [Cleaning](screenshots/cleaning.png)
- [Validation](screenshots/validation.png)
- [Reconciliation](screenshots/reconciliation.png)
- [Analytics](screenshots/analytics.png)
- [Reports](screenshots/reports.png)
- [History](screenshots/history.png)
- [Settings](screenshots/settings.png)

</details>

## What it does

### Upload and preserve

Upload CSV or `.xlsx` transaction files. The original upload is kept separately from its normalized database records; cleaning does not overwrite the source file or the recorded source values.

### Clean and validate

Headers and whitespace are normalized, parseable dates are standardized, and financial fields are parsed as numeric values. Validation findings can include the source record ID, column, original value, category, explanation, severity, and a suggested correction.

The configured checks cover:

- Missing required fields and duplicate transaction IDs
- Unparseable dates and non-numeric or negative amounts
- Currency codes, tax categories, and transaction statuses
- Entity-name formatting and selected financial inconsistencies

Findings are presented for review. Duplicate or invalid rows are **not silently discarded**.

### Reconcile and review

Compare source and normalized revenue and expense totals. The reconciliation view also reports missing and repeated transaction IDs. When data-quality issues are detected, the result calls for review even if the arithmetic totals match.

### Analyze and report

The dashboard and analytics page summarize revenue, expenses, recorded tax amounts, monthly trends, entities, and categories. These are grouped from supplied data; currency amounts are not converted into a common currency.

Download a report for an upload:

| Format | Contents |
|---|---|
| **Excel** | Summary, validation exceptions, reconciliation, financial summary, and cleaned records |
| **CSV** | Validation-exception report |
| **PDF** | Processing summary and a concise exception overview |

Reports are generated for a selected batch. Spreadsheet exports escape formula-like text values.

## Run locally

**Requirements:** Python 3.12 or newer and pip.

From Windows PowerShell, in the project directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Open `.env` and set your own secret key and administrator password. Then initialize the database and start the development server:

```powershell
flask --app run.py init-db
flask --app run.py run
```

Visit [http://127.0.0.1:5000](http://127.0.0.1:5000) and sign in using the `ADMIN_USERNAME` and `ADMIN_PASSWORD` values from `.env`. The initializer creates the configured user only when that username does not already exist. **The example values are for setup only; replace them before running the initializer.**

To run the tests:

```powershell
pytest
```

## Synthetic sample data

The included dataset is fictional and reproducible. It contains at least 10,000 transaction rows by default, with deliberately seeded duplicates, missing values, invalid dates, and invalid financial or classification values.

Regenerate the default file:

```powershell
flask --app run.py generate-sample
```

Choose another output path or generate more rows:

```powershell
flask --app run.py generate-sample --rows 12000 --output data/review_sample.csv
```

The generator requires a minimum of 10,000 rows. The generated default file is provided at [`data/synthetic_transactions.csv`](data/synthetic_transactions.csv).

## Accepted file fields

Column names are matched without regard to case; spaces and punctuation are normalized.

| Field | Required | Notes |
|---|:---:|---|
| Transaction ID | Yes | Repeated identifiers are flagged, not removed |
| Transaction Date | Yes | Must parse as a valid date |
| Entity Name | Yes | Checked for a present, usable value |
| Tax Category | Yes | Compared with the configured illustrative category list |
| Currency | Yes | Checked against the supported ISO 4217 code set |
| Transaction Status | Yes | Checked against the supported status set |
| Department | No | Stored with the transaction when provided |
| Revenue | No | Normalized when present |
| Expenses | No | Normalized when present |
| Tax Amount | No | Stored as a source amount; not treated as calculated tax liability |

**Upload limits:** 16 MB by default and a maximum of 100,000 rows per file. The request-size limit can be adjusted with `MAX_CONTENT_LENGTH`; the row ceiling is enforced in processing logic.

## Configuration

Copy `.env.example` to `.env`. Values in the environment take precedence over the application defaults.

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | Local-development placeholder | Signs Flask sessions and CSRF tokens. Use a unique random value outside local development. |
| `DATABASE_URL` | `sqlite:///tax_processing.db` | SQLAlchemy connection string. Relative SQLite database paths use Flask's instance directory. |
| `UPLOAD_FOLDER` | `instance/uploads` | Location for unchanged source uploads. |
| `MAX_CONTENT_LENGTH` | `16777216` | Maximum request size, in bytes. |
| `ADMIN_USERNAME` | `admin` | Username created by `flask init-db`, if it does not exist. |
| `ADMIN_PASSWORD` | `change-this-before-use` | Initial password. Replace before initialization; the app stores a password hash. |
| `FLASK_DEBUG` | `false` | Enables Flask debug mode only when set to `true`. |
| `PUBLIC_BASE_URL` | Unset | Optional public HTTPS origin for canonical URLs and trusted-host configuration. |

### SQLite and MySQL

SQLite is the default and is suitable for local development. To connect to MySQL, use the included PyMySQL driver and set a correctly URL-encoded SQLAlchemy URL, for example:

```dotenv
DATABASE_URL=mysql+pymysql://app_user:encoded_password@db-host/tax_data
```

The automated tests use SQLite. A production MySQL server, credentials, backups, network security, and schema rollout process must be provisioned separately.

### Custom domain

Set `PUBLIC_BASE_URL` to the HTTPS origin for a domain you control:

```dotenv
PUBLIC_BASE_URL=https://tax.example.com
```

The application can then emit canonical URLs for that origin and trust its host. Point the domain's DNS records to your hosting provider and configure HTTPS there. **The project cannot register a domain or configure external DNS or TLS on your behalf.**

## Project structure

```text
tax_app/
├── __init__.py                App factory, extensions, and CLI commands
├── forms.py                   CSRF-protected login and upload forms
├── models.py                  SQLAlchemy data models
├── routes/
│   ├── auth.py                Login and logout
│   └── main.py                Dashboard, processing, analytics, and reports
├── services/
│   └── processing.py          Normalization, validation, and reconciliation
├── sample_data.py             Fictional sample-data generator
├── static/
│   ├── css/app.css            Responsive application styles
│   └── favicon.svg            Ledgerwise brand icon
└── templates/                 Jinja page templates
data/
└── synthetic_transactions.csv Fictional sample dataset
screenshots/                   Browser captures of application pages
tests/                          Upload, processing, reports, and data tests
run.py                          Flask application entry point
requirements.txt                Python dependencies
```

## Data model

The SQLAlchemy schema defines users, uploaded batches, financial transactions, validation rules, validation errors, reconciliation results, processing history, and generated-report records. Transaction rows keep separate JSON values for the original parsed input and its cleaned representation.

## Important notes

- **Not tax software:** This portfolio application does not prepare or file tax returns and does not provide tax advice or official tax calculations.
- **Review is required:** Validation rules are practical data-quality checks, not authoritative accounting or tax classifications.
- **Currencies are not converted:** Reports may aggregate amounts from different currencies. Do not compare mixed-currency totals as if they were a single-currency balance.
- **Protect financial data:** Configure strong credentials, HTTPS, least-privilege storage/database access, backups, and appropriate retention policies before handling sensitive business data.
- **Use a production server for deployment:** Flask's development server is intended for local development only.
