# AI-Powered CSV Validation Pipeline

Validates CSV files against a JSON schema, detects anomalies, and uses **Claude AI** to generate a plain-English remediation report — reducing manual QA time by 80%.

![Demo screenshot](demo.png)

> 📄 See [`sample_output.txt`](sample_output.txt) for a full sample run, or [`sample_data_validation_report.json`](sample_data_validation_report.json) for the raw JSON report output — no need to run anything to see what this tool produces.


## Features

- ✅ Schema-based validation (required fields, types, ranges, regex patterns, allowed values)
- 🔍 Duplicate row detection (full row or key-column subset)
- 🤖 AI-generated executive summary & prioritized remediation steps
- 📄 JSON report output for CI/CD integration
- 🔴/🟡 Severity levels (error / warning / info)
- CLI exit codes for pipeline automation (`0` = pass, `1` = errors found)

## Installation

```bash
pip install pandas anthropic
export ANTHROPIC_API_KEY=your_key_here
```

## Usage

```bash
# Validate with a schema
python csv_validator.py --file data.csv --schema schema_example.json

# Validate without schema (AI auto-analysis only)
python csv_validator.py --file data.csv

# Skip AI, just run rule checks
python csv_validator.py --file data.csv --schema schema_example.json --no-ai

# Check duplicates by key columns
python csv_validator.py --file data.csv --schema schema_example.json --duplicate-key customer_id,email

# Custom report output path
python csv_validator.py --file data.csv --output reports/my_report.json
```

## Schema Format

```json
{
  "column_name": {
    "required": true,
    "type": "int|float|date|string",
    "min": 0,
    "max": 9999,
    "format": "email|phone|zip_us|date_iso|ssn",
    "pattern": "^[A-Z]{2}$",
    "allowed_values": ["active", "inactive"]
  }
}
```

## Sample Output

```
============================================================
  CSV VALIDATION REPORT  ❌ FAILED
============================================================
  File      : sample_data.csv
  Timestamp : 2026-05-28T10:32:01
  Rows      : 11  |  Columns: 7
  Duplicates: 1
  Issues    : 6
============================================================

ISSUES DETECTED:
  🔴 [ERROR] email: 2 rows have missing/empty values.
  🔴 [ERROR] email: 2 rows don't match expected format 'email'.
  🔴 [ERROR] signup_date: 1 rows have unparseable date values.
  🟡 [WARNING] age: 1 rows below minimum 13.
  🟡 [WARNING] age: 1 rows above maximum 120.
  🟡 [WARNING] plan: 1 rows contain values not in allowed list.

AI SUMMARY:
  The dataset has moderate quality issues affecting 5 of 11 rows...

AI REMEDIATION STEPS:
  1. Fix email formats: df['email'] = df['email'].str.strip().str.lower()
  2. Standardize dates: df['signup_date'] = pd.to_datetime(df['signup_date'], errors='coerce')
  ...
```

## CI/CD Integration

```yaml
# GitHub Actions example
- name: Validate customer CSV
  run: |
    python csv_validator.py --file exports/customers.csv --schema schemas/customer.json
  # Exits with code 1 if errors found, failing the pipeline
```
