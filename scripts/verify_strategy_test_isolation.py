"""Verify that the whole backend suite leaves the configured workspace archive unchanged."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def inventory(root):
    files = [root/"tradeforge.sqlite3",root/"tradeforge.sqlite3-wal"]
    files.extend(path for path in (root/"market").rglob("*") if path.is_file())
    result = {}
    for path in sorted(files):
        if path.exists():
            with path.open("rb") as stream:
                result[path.relative_to(root).as_posix()] = hashlib.file_digest(stream,"sha256").hexdigest()
    return result


def main():
    storage = Path(os.environ.get("TRADEFORGE_STORAGE","storage"))
    root = Path(os.environ.get("TRADEFORGE_DATA_DIR",storage/"research")).resolve()
    before = inventory(root)
    status = subprocess.call([sys.executable,"-m","pytest","-q"])
    after = inventory(root)
    changed = sorted(key for key in before.keys()|after.keys() if before.get(key)!=after.get(key))
    print(json.dumps({"archive_files_before":len(before),"archive_files_after":len(after),
                      "unchanged":not changed,"changed_files":len(changed),"pytest_exit":status}))
    return status or bool(changed)


if __name__=="__main__":
    raise SystemExit(main())
