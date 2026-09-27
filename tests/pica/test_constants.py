"""constants 模块冒烟测试"""

import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pica.core.constants import AUTH_ERROR_CODES, CATEGORIES, ORDERS, RANK_TYPES


def test_categories_nonempty():
    assert len(CATEGORIES) > 10


def test_auth_error_codes_are_strings():
    assert all(isinstance(c, str) for c in AUTH_ERROR_CODES)
    assert {"401", "1005", "1008"} <= AUTH_ERROR_CODES


def test_rank_types():
    assert "H24" in RANK_TYPES
    assert "D7" in RANK_TYPES
    assert "D30" in RANK_TYPES


def test_orders():
    assert "ua" in ORDERS