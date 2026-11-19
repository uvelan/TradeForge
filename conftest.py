"""Put every pytest process on a disposable archive before test collection."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory


def pytest_configure(config):
    config.tradeforge_test_archive = TemporaryDirectory(prefix="tradeforge-pytest-")
    config.tradeforge_previous_storage = {key:os.environ.get(key)
                                         for key in ("TRADEFORGE_STORAGE","TRADEFORGE_DATA_DIR")}
    root = Path(config.tradeforge_test_archive.name).resolve()
    os.environ["TRADEFORGE_STORAGE"] = str(root)
    os.environ["TRADEFORGE_DATA_DIR"] = str(root/"research")


def pytest_unconfigure(config):
    if hasattr(config,"tradeforge_test_archive"):
        for key,value in config.tradeforge_previous_storage.items():
            if value is None:
                os.environ.pop(key,None)
            else:
                os.environ[key] = value
        config.tradeforge_test_archive.cleanup()
