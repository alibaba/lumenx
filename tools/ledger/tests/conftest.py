import os
import sys

# 让测试可以直接 import tools/ledger 下的平铺模块（schema/db/import_storyboard/...）
HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER_DIR = os.path.dirname(HERE)
if LEDGER_DIR not in sys.path:
    sys.path.insert(0, LEDGER_DIR)
