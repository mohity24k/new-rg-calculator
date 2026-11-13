"""Export the stored feedback (accept / reject decisions and comments) to CSV.

    python scripts/export_feedback.py [--data-dir data/runtime] [--out feedback.csv]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gms import feedback, settings  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=None, help="default: GMS_DATA_DIR or data/runtime")
    ap.add_argument("--out", default="feedback.csv")
    a = ap.parse_args()
    db = Path(a.data_dir or settings.load().data_dir) / "feedback.db"
    Path(a.out).write_bytes(feedback.to_csv(db))
    print(f"{len(feedback.rows(db))} records written to {a.out}")


if __name__ == "__main__":
    main()
