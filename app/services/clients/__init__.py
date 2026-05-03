"""按客户拆分的结算解析扩展（与 app/client_rules/rules/*.json 对应）。"""

from . import Demo_Alt
from . import DesaySV
from . import Geely_ATO
from . import Geely_ITO

CLIENT_MODULES: dict[str, object] = {
    "geely_ato": Geely_ATO,
    "geely_ito": Geely_ITO,
    "desay": DesaySV,
    "demo_alt": Demo_Alt,
}

__all__ = ["CLIENT_MODULES", "Geely_ATO", "Geely_ITO", "DesaySV", "Demo_Alt"]
