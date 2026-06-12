"""
AI-Powered CSV Validation Pipeline
====================================
Validates CSV files against a schema, detects anomalies,
and uses Claude AI to generate a plain-English remediation report.

Usage:
    python csv_validator.py --file data.csv --schema schema.json
    python csv_validator.py --file data.csv  # auto-infer schema with AI
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
import pandas as pd


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ColumnIssue:
    column: str
    issue_type: str          # "missing" | "type_mismatch" | "out_of_range" | "pattern" | "duplicate"
    row_indices: list[int]
    details: str
    severity: str            # "error" | "warning" | "info"


@dataclass
class ValidationReport:
    file_path: str
    timestamp: str
    total_rows: int
    total_columns: int
    issues: list[ColumnIssue] = field(default_factory=list)
    duplicate_rows: int = 0
    ai_summary: str = ""
    ai_remediation: list[str] = field(default_factory=list)
    passed: bool = True


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

BUILT_IN_PATTERNS = {
    "email":   r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    "phone":   r"^\+?[\d\s\-().]{7,20}$",
    "zip_us":  r"^\d{5}(-\d{4})?$",
    "date_iso": r"^\d{4}-\d{2}-\d{2}$",
    "ssn":     r"^\d{3}-\d{2}-\d{4}$",
}

DEFAULT_SCHEMA: dict[str, Any] = {}   # empty = auto-detect only


def load_schema(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Core validation logic
# ---------------------------------------------------------------------------

def validate(df: pd.DataFrame, schema: dict) -> list[ColumnIssue]:
    issues: list[ColumnIssue] = []

    for col, rules in schema.items():
        if col not in df.columns:
            issues.append(ColumnIssue(
                column=col, issue_type="missing",
                row_indices=[], severity="error",
                details=f"Expected column '{col}' not found in file."
            ))
            continue

        series = df[col]

        # Required / null check
        if rules.get("required", False):
            null_idx = series[series.isna() | (series.astype(str).str.strip() == "")].index.tolist()
            if null_idx:
                issues.append(ColumnIssue(
                    column=col, issue_type="missing",
                    row_indices=null_idx, severity="error",
                    details=f"{len(null_idx)} rows have missing/empty values."
                ))

        # Type check
        expected_type = rules.get("type", "")
        if expected_type in ("int", "float"):
            coerced = pd.to_numeric(series, errors="coerce")
            bad_idx = coerced[coerced.isna() & series.notna()].index.tolist()
            if bad_idx:
                issues.append(ColumnIssue(
                    column=col, issue_type="type_mismatch",
                    row_indices=bad_idx, severity="error",
                    details=f"{len(bad_idx)} rows cannot be converted to {expected_type}."
                ))
        elif expected_type == "date":
            coerced = pd.to_datetime(series, errors="coerce")
            bad_idx = coerced[coerced.isna() & series.notna()].index.tolist()
            if bad_idx:
                issues.append(ColumnIssue(
                    column=col, issue_type="type_mismatch",
                    row_indices=bad_idx, severity="warning",
                    details=f"{len(bad_idx)} rows have unparseable date values."
                ))

        # Range check
        if "min" in rules or "max" in rules:
            numeric = pd.to_numeric(series, errors="coerce")
            if "min" in rules:
                bad = numeric[numeric < rules["min"]].index.tolist()
                if bad:
                    issues.append(ColumnIssue(
                        column=col, issue_type="out_of_range",
                        row_indices=bad, severity="warning",
                        details=f"{len(bad)} rows below minimum {rules['min']}."
                    ))
            if "max" in rules:
                bad = numeric[numeric > rules["max"]].index.tolist()
                if bad:
                    issues.append(ColumnIssue(
                        column=col, issue_type="out_of_range",
                        row_indices=bad, severity="warning",
                        details=f"{len(bad)} rows above maximum {rules['max']}."
                    ))

        # Pattern / format check
        pattern_key = rules.get("format", "")
        pattern = BUILT_IN_PATTERNS.get(pattern_key, rules.get("pattern", ""))
        if pattern:
            non_null = series.dropna().astype(str)
            bad_idx = non_null[~non_null.str.match(pattern)].index.tolist()
            if bad_idx:
                issues.append(ColumnIssue(
                    column=col, issue_type="pattern",
                    row_indices=bad_idx, severity="error",
                    details=f"{len(bad_idx)} rows don't match expected format '{pattern_key or pattern}'."
                ))

        # Allowed values
        allowed = rules.get("allowed_values", [])
        if allowed:
            bad_idx = series[~series.isin(allowed) & series.notna()].index.tolist()
            if bad_idx:
                issues.append(ColumnIssue(
                    column=col, issue_type="pattern",
                    row_indices=bad_idx, severity="warning",
                    details=f"{len(bad_idx)} rows contain values not in allowed list: {allowed}."
                ))

    return issues


def check_duplicates(df: pd.DataFrame, subset: list[str] | None = None) -> int:
    return int(df.duplicated(subset=subset).sum())


# ---------------------------------------------------------------------------
# AI summary via Claude
# ---------------------------------------------------------------------------

def ai_summarize(report: ValidationReport, df: pd.DataFrame) -> ValidationReport:
    client = anthropic.Anthropic()

    issues_text = "\n".join(
        f"- [{i.severity.upper()}] Column '{i.column}': {i.details} (type: {i.issue_type})"
        for i in report.issues
    ) or "No schema violations detected."

    sample_columns = ", ".join(df.columns[:15].tolist())

    prompt = f"""You are a data quality engineer reviewing a CSV validation report.

FILE: {report.file_path}
ROWS: {report.total_rows} | COLUMNS: {report.total_columns}
DUPLICATE ROWS: {report.duplicate_rows}
COLUMNS PRESENT: {sample_columns}

ISSUES FOUND:
{issues_text}

Please provide:
1. A 2-3 sentence executive summary of the data quality state.
2. A numbered list of 4-6 concrete remediation steps (specific pandas/Python code snippets where helpful).
3. A priority order for fixing the issues.

Be concise and actionable. Format remediation steps as a JSON array of strings in a ```json block."""

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text

    # Extract summary (everything before the json block)
    json_match = re.search(r"```json\s*([\s\S]+?)\s*```", raw)
    if json_match:
        try:
            steps = json.loads(json_match.group(1))
            report.ai_remediation = steps if isinstance(steps, list) else [str(steps)]
        except json.JSONDecodeError:
            report.ai_remediation = ["See full AI output above."]
        report.ai_summary = raw[:raw.find("```")].strip()
    else:
        report.ai_summary = raw
        report.ai_remediation = []

    return report


# ---------------------------------------------------------------------------
# Report output
# ---------------------------------------------------------------------------

def print_report(report: ValidationReport) -> None:
    status = "✅ PASSED" if report.passed else "❌ FAILED"
    print(f"\n{'='*60}")
    print(f"  CSV VALIDATION REPORT  {status}")
    print(f"{'='*60}")
    print(f"  File      : {report.file_path}")
    print(f"  Timestamp : {report.timestamp}")
    print(f"  Rows      : {report.total_rows:,}  |  Columns: {report.total_columns}")
    print(f"  Duplicates: {report.duplicate_rows}")
    print(f"  Issues    : {len(report.issues)}")
    print(f"{'='*60}\n")

    if report.issues:
        print("ISSUES DETECTED:")
        for issue in sorted(report.issues, key=lambda x: x.severity):
            icon = "🔴" if issue.severity == "error" else "🟡" if issue.severity == "warning" else "🔵"
            sample = f" (rows: {issue.row_indices[:5]}{'...' if len(issue.row_indices) > 5 else ''})" if issue.row_indices else ""
            print(f"  {icon} [{issue.severity.upper()}] {issue.column}: {issue.details}{sample}")
        print()

    if report.ai_summary:
        print("AI SUMMARY:")
        print(f"  {report.ai_summary}\n")

    if report.ai_remediation:
        print("AI REMEDIATION STEPS:")
        for i, step in enumerate(report.ai_remediation, 1):
            print(f"  {i}. {step}")
        print()


def save_report(report: ValidationReport, output_path: str) -> None:
    data = asdict(report)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  📄 Report saved to: {output_path}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AI-Powered CSV Validation Pipeline")
    parser.add_argument("--file", required=True, help="Path to CSV file")
    parser.add_argument("--schema", default="", help="Path to JSON schema file")
    parser.add_argument("--output", default="", help="Output path for JSON report")
    parser.add_argument("--no-ai", action="store_true", help="Skip AI summarization")
    parser.add_argument("--duplicate-key", default="", help="Comma-separated columns to check for duplicates")
    args = parser.parse_args()

    # Load data
    print(f"\n📂 Loading: {args.file}")
    try:
        df = pd.read_csv(args.file)
    except Exception as e:
        print(f"❌ Failed to read CSV: {e}")
        sys.exit(1)

    print(f"   {len(df):,} rows × {len(df.columns)} columns loaded.")

    # Load schema
    schema = DEFAULT_SCHEMA
    if args.schema:
        print(f"📋 Loading schema: {args.schema}")
        schema = load_schema(args.schema)

    # Run validation
    print("🔍 Running validation checks...")
    issues = validate(df, schema)

    dup_subset = [c.strip() for c in args.duplicate_key.split(",")] if args.duplicate_key else None
    dup_count = check_duplicates(df, dup_subset)

    has_errors = any(i.severity == "error" for i in issues)

    report = ValidationReport(
        file_path=args.file,
        timestamp=datetime.now().isoformat(),
        total_rows=len(df),
        total_columns=len(df.columns),
        issues=issues,
        duplicate_rows=dup_count,
        passed=not has_errors,
    )

    # AI summary
    if not args.no_ai:
        print("🤖 Generating AI analysis...")
        report = ai_summarize(report, df)

    # Output
    print_report(report)

    out_path = args.output or args.file.replace(".csv", "_validation_report.json")
    save_report(report, out_path)

    sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
