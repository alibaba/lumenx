"""export_manifest：v1.1 列序 CSV 导出视图（utf-8-sig + 原子写）。"""

import csv

from export_manifest import export_manifest
from schema import MANIFEST_COLUMNS

EXPECTED_V11_COLUMNS = [
    "asset_id", "type", "parent_ids", "description", "source", "status",
    "attempt_count", "fingerprint", "content_hash", "output_path", "cost",
    "created_at", "updated_at", "schema_version", "note",
]


def test_manifest_columns_are_v11(tmp_path):
    assert list(MANIFEST_COLUMNS) == EXPECTED_V11_COLUMNS


def test_export_rows_and_encoding(tmp_path):
    from db import Ledger

    db = str(tmp_path / "l.db")
    out = str(tmp_path / "manifest.csv")
    led = Ledger(db)
    led.update("CH-01", type="character", description="梁宸", source="script",
               fingerprint="fp001", note="来自资产卡")
    led.update("SB-001", type="storyboard", description="E01-S01 测试", source="script",
               parent_ids="CH-01;PR-01;SC-01")
    led.close()

    n = export_manifest(db, out)
    assert n == 2

    raw = open(out, "rb").read()
    assert raw.startswith(b"\xef\xbb\xbf")            # utf-8-sig BOM，Excel 友好

    rows = list(csv.reader(open(out, encoding="utf-8-sig")))
    assert rows[0] == EXPECTED_V11_COLUMNS
    assert len(rows) == 3                              # 表头 + 2 行
    by_id = {r[0]: r for r in rows[1:]}
    assert by_id["CH-01"][2] == ""                     # parent_ids 为空
    assert by_id["CH-01"][6] == "0"                    # attempt_count 默认 0
    assert by_id["CH-01"][13] == "1.1"                 # schema_version
    assert by_id["SB-001"][2] == "CH-01;PR-01;SC-01"
    # data JSON 扩展列不进导出视图
    assert all("data" not in col for col in rows[0])
