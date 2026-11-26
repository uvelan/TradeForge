"""Replay the reviewed V1 API writer in a disposable, isolated research database."""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from fastapi.testclient import TestClient

BASE = "fdf01b1076a6b925055e4f1ae99ff237c9491e34"
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "frontend/public/examples/controlled-statement.json"


def cases():
    base = json.loads(EXAMPLE.read_bytes())
    yield "control", base
    for case in ("submicro", "exact", "one_micro_late", "period_only", "successor", "gap", "inside"):
        s = json.loads(EXAMPLE.read_bytes())
        spec = s["specs"][0]
        end = s["end"]
        if case in ("submicro", "exact", "one_micro_late"):
            s["marks"][0]["available"] = {
                "submicro": end.replace("Z", ".0000001Z"),
                "one_micro_late": end.replace("Z", ".000001Z"), "exact": end,
            }[case]
        else:
            boundary = s["marks"][0]["as_of"] if case in ("gap", "inside") else end
            spec["end"] = boundary
            if case in ("successor", "gap", "inside"):
                s["specs"].append(dict(spec, id="next", start=boundary if case != "gap" else end,
                                       end="2026-01-01T00:00:00Z", leverage="50" if case != "gap" else "100"))
        yield case, s


def seed(directory):
    directory.mkdir(parents=True, exist_ok=True)
    os.environ["TRADEFORGE_DATA_DIR"] = str(directory)
    with tempfile.TemporaryDirectory() as work:
        archive = subprocess.check_output(
            ["git", "archive", "--format=zip", BASE, "research_backend/app", "tradeforge"], cwd=ROOT
        )
        with zipfile.ZipFile(io.BytesIO(archive)) as snapshot:
            snapshot.extractall(work)
        sys.path.insert(0,work)
        os.environ["TRADEFORGE_STORAGE"] = str(directory.parent)
        from research_backend.app.main import app
        with TestClient(app) as client:
            for name, s in cases():
                reg = client.post("/api/statements",json=s)
                reg.raise_for_status()
                identity = reg.json()["id"]
                result = client.post(f"/api/statements/{identity}/advance?cursor=0")
                result.raise_for_status()
                report = result.json()["report"]
                print(json.dumps(dict(case=name,id=identity,status=report["status"],spec=report["positions"][0].get("spec"),
                                      margin=report["margin_minor"], residuals=[report[x+"_residual_minor"] for x in ("balance","equity","margin")],
                                      issues=report["issues"])))
        sys.path.remove(work)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.directory:
        if args.directory.joinpath("tradeforge.sqlite3").exists():
            parser.error("Historical seeding requires a new test database")
        seed(args.directory.resolve())
    else:
        with tempfile.TemporaryDirectory() as tmp:
            seed(Path(tmp))
