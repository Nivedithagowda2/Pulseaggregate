"""Shared helpers for the command-line scripts."""
import json
import math
from pathlib import Path

from pulse.config import settings

# Demo-data layout (the generator and the simulators agree on these).
PRODUCTS_PER_TENANT = 50
CUSTOMERS_PER_TENANT = 200


def load_keys(path=None):
    """Returns {tenant_id(int): api_key} from the file written by generate_data.py."""
    path = Path(path or settings.keys_file)
    if not path.exists():
        raise SystemExit(f"API key file not found: {path}. Run scripts/generate_data.py first.")
    data = json.loads(path.read_text())
    return {int(tid): info["api_key"] for tid, info in data["tenants"].items()}


def pct(values, p):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(1, math.ceil(p / 100.0 * len(ordered))) - 1]
