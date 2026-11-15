from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, URL


def open_database(root: Path) -> Engine:
    path = root / "database" / "trading.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(URL.create("sqlite", database=str(path)))

    @event.listens_for(engine, "connect")
    def configure(connection, _record):
        for pragma in (
            "busy_timeout=5000", "foreign_keys=ON", "journal_mode=WAL", "synchronous=FULL"
        ):
            connection.execute(f"PRAGMA {pragma}")

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    return engine
