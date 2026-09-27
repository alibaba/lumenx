"""manifest.csv 导出视图：SQLite 账本 → workflow v1.1 列序的人工可读 CSV。

manifest.csv 不再是权威账本（权威在 SQLite/WAL），本脚本只读导出：
- 列序 = schema.MANIFEST_COLUMNS（v1.0 九列 + v1.1 六新列），不含 data JSON 扩展列；
- utf-8-sig 带 BOM，Excel 直接打开不乱码；
- 原子写（临时文件 + os.replace），导出中断不会留下半个 CSV。
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import Ledger  # noqa: E402
from schema import MANIFEST_COLUMNS  # noqa: E402

DEFAULT_DB_PATH = r"C:\Users\YY\剧本项目\ledger\ledger.db"
DEFAULT_OUT_PATH = r"C:\Users\YY\剧本项目\ledger\manifest.csv"


def export_manifest(db_path: str, out_path: str) -> int:
    ledger = Ledger(db_path)
    try:
        rows = ledger.query()
    finally:
        ledger.close()

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".csv", dir=os.path.dirname(os.path.abspath(out_path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(MANIFEST_COLUMNS)
            for row in rows:
                writer.writerow([row.get(col, "") for col in MANIFEST_COLUMNS])
        os.replace(tmp, out_path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="账本 → manifest.csv 导出视图")
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    ap.add_argument("--out", default=DEFAULT_OUT_PATH)
    args = ap.parse_args()

    n = export_manifest(args.db, args.out)
    print(f"导出 {n} 行 -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
