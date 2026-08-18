"""
data_export.py
----------------
Utilities to package calculated metrics into a clean, downloadable
CSV / JSON summary report.
"""

import json
import pandas as pd


def build_summary_dataframe(metrics_dict: dict) -> pd.DataFrame:
    """Convert a flat {metric_name: value} dict into a tidy two-column DataFrame."""
    rows = [{"Metric": k, "Value": v} for k, v in metrics_dict.items()]
    return pd.DataFrame(rows)


def build_combined_report(route_name: str,
                           mass_metrics: dict = None,
                           eco_scale: dict = None,
                           hazard_metrics: dict = None) -> dict:
    """Merge all metric categories for a single route into one nested dict."""
    report = {"Route": route_name}
    if mass_metrics:
        report["Mass-Based Metrics"] = mass_metrics
    if eco_scale:
        report["Eco-Scale"] = eco_scale
    if hazard_metrics:
        report["Global Hazard & Toxicity"] = hazard_metrics
    return report


def flatten_report(report: dict) -> dict:
    """Flatten a nested report dict into a single-level dict for CSV export."""
    flat = {}
    for section, content in report.items():
        if isinstance(content, dict):
            for k, v in content.items():
                flat[f"{section} :: {k}"] = v
        else:
            flat[section] = content
    return flat


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def to_json_bytes(data) -> bytes:
    return json.dumps(data, indent=2, default=str).encode("utf-8")
