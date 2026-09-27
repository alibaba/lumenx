"""模型单价表加载与查价（litellm "价格表" 思想的本地化，不引入 litellm）。

- prices.json 与本文件同目录；模型 → {时长秒: 单价(CNY)}；
- 查价：duration 整数 → 字符串键；未列出时长回退 "default" 键；都没有 → None（unpriced）；
- unpriced 不阻塞生成，成本记 "0" 并在 events 留痕（权责发生制下宁缺勿错）。
"""

from __future__ import annotations

import json
import os
from typing import Optional

PRICES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prices.json")

DEFAULT_KEY = "default"


def load_prices(path: str = PRICES_PATH) -> dict:
    """加载单价表 → {model: {duration_key(str): price(float)}}；缺文件/坏 JSON 报错。"""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    models = raw.get("models")
    if not isinstance(models, dict):
        raise ValueError(f"{path}: 缺少 models 字段")
    table: dict[str, dict[str, float]] = {}
    for model, spec in models.items():
        prices = spec.get("price_by_duration_cny") if isinstance(spec, dict) else None
        if not isinstance(prices, dict) or not prices:
            raise ValueError(f"{path}: 模型 {model} 缺 price_by_duration_cny")
        table[model] = {str(k): float(v) for k, v in prices.items()}
    return table


def lookup_price(prices: dict, model: str, duration: Optional[int] = None) -> Optional[float]:
    """查模型单价：先按时长秒精确键，未命中回退 default；无表无兜底 → None。"""
    table = prices.get(model)
    if not table:
        return None
    if duration is not None:
        hit = table.get(str(int(duration)))
        if hit is not None:
            return hit
    return table.get(DEFAULT_KEY)


def cost_for(prices: dict, model: str, duration: Optional[int] = None, qty: int = 1) -> Optional[float]:
    """单次生成成本 = 单价 × qty；unpriced → None。"""
    unit = lookup_price(prices, model, duration)
    if unit is None:
        return None
    return round(unit * qty, 4)
