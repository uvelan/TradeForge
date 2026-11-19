"""Explicit quarantined fixture identities from the read-only TF-I09B inventory."""
import json
from pathlib import Path

_INVENTORY = json.loads(Path(__file__).with_name("quarantined_capture.json").read_text(encoding="utf-8"))
SYNTHETIC_SESSIONS = frozenset(item["id"] for item in _INVENTORY["sessions"])
SYNTHETIC_PARTITIONS = frozenset(item["dataset_id"] for item in _INVENTORY["partitions"])
SYNTHETIC_MANIFESTS = frozenset(item["id"] for item in _INVENTORY["manifests"])


def require_market_partition(dataset_id):
    if dataset_id in SYNTHETIC_PARTITIONS:
        raise ValueError("QUARANTINED_SYNTHETIC_CAPTURE")
