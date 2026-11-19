"""Repeat the reviewed benchmark while preserving its original evidence files."""
import json
from pathlib import Path
from runpy import run_path


def write_result(name, value):
    if name == "benchmark.json":
        Path(__file__).with_name(name).write_text(json.dumps(value, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    source = Path("docs/implementation/evidence/TF-I09A-integrity/benchmark.py")
    main = run_path(str(source))["main"]
    main.__globals__["emit"] = write_result
    main()
