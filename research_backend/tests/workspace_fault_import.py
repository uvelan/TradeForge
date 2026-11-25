"""Child-process fault probe for the local market workspace drill only."""
import errno
import os
from pathlib import Path
import sys
from tempfile import gettempdir
from time import monotonic, sleep

from fastapi.testclient import TestClient


def main(mode):
    root = Path(os.environ["TRADEFORGE_STORAGE"]).resolve()
    if (root.parent != Path(gettempdir()).resolve() or not root.name.startswith("tradeforge-e2e-")
            or Path(os.environ["TRADEFORGE_DATA_DIR"]).resolve() != root/"research"
            or not (root/"fault-probe-enabled").is_file()):
        raise ValueError("Fault probe requires its own temporary workspace")
    sys.modules['MetaTrader5'] = None
    from tradeforge import publication
    from research_backend.app.main import app
    from research_backend.app.db import one
    original = publication.install
    def install(spool, checksum, path):
        if mode == "disk-full":
            raise OSError(errno.ENOSPC, "controlled destination volume exhaustion")
        original(spool, checksum, path)
        (root/"uncommitted-file").write_text(path.name)
        deadline = monotonic()+35
        while not (root/"release-writer").exists() and monotonic()<deadline:
            sleep(0.05)
        os._exit(75)
    publication.install = install
    path = Path(__file__).resolve().parents[2]/"storage/t06-input/14h_ticks.bi5"
    assert mode in ("interrupt","disk-full")
    with TestClient(app,base_url="http://localhost") as client:
        result = client.post("/api/market/import/reviewed",files={"file":(path.name,path.read_bytes())})
        assert mode == "disk-full" and result.status_code == 507, result.text
        print(result.status_code,result.json(),one("SELECT count(*) AS visible FROM datasets"))


if __name__ == "__main__":
    main(sys.argv[1])
