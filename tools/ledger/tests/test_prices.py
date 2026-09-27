"""prices.py 单元测试：加载 / 查价 / 兜底 / 坏表报错。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import prices  # noqa: E402


def test_load_real_prices_table():
    table = prices.load_prices()  # 仓库真实 prices.json
    assert "kling-3.0" in table
    assert table["kling-3.0"]["5"] == 2.0
    assert table["kling-3.0"]["15"] == 6.0
    assert table["mock-video"]["10"] == 1.0


def test_lookup_exact_and_default_fallback():
    p = {"m1": {"5": 2.0, "10": 4.0}, "m2": {"default": 0.7}}
    assert prices.lookup_price(p, "m1", 5) == 2.0
    assert prices.lookup_price(p, "m1", 7) is None          # 无该时长且无 default
    assert prices.lookup_price(p, "m2", 8) == 0.7           # default 兜底
    assert prices.lookup_price(p, "unknown", 5) is None     # 无模型


def test_cost_for_qty_and_unpriced():
    p = {"m": {"5": 2.0}}
    assert prices.cost_for(p, "m", 5) == 2.0
    assert prices.cost_for(p, "m", 5, qty=3) == 6.0
    assert prices.cost_for(p, "m", 15) is None


def test_load_rejects_bad_table(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"models": {"m": {}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="price_by_duration_cny"):
        prices.load_prices(str(bad))
