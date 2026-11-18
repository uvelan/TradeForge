"""Read-only inventory for the running workspace's local research database."""
from datetime import datetime
import json
import sqlite3

from research_backend.app.config import DATABASE
from research_backend.app.arrival_routes import assess
from research_backend.app.market import FRAME_MIN
from research_backend.app.smt_causal import FrozenFeed


def main():
    if not DATABASE.is_file():
        print("No local research SQLite catalog")
        return
    with sqlite3.connect(DATABASE.as_uri()+"?mode=ro",uri=True) as connection:
        connection.row_factory=sqlite3.Row
        summary=connection.execute("""SELECT provider,kind,count(*) partitions,sum(rows_count) records
            FROM datasets GROUP BY provider,kind ORDER BY provider,kind""").fetchall()
        print(json.dumps([dict(item) for item in summary]))
        count=connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='arrival_manifests'").fetchone()
        print("sealed_recorded_manifests",connection.execute("SELECT count(*) FROM arrival_manifests").fetchone()[0] if count else 0)
        for provider in ("MT5","DUKASCOPY"):
            row=connection.execute("""SELECT * FROM datasets WHERE provider=? AND kind='candles'
                ORDER BY created_at DESC LIMIT 1""",(provider,)).fetchone()
            if not row:
                continue
            step=FRAME_MIN[row["timeframe"]]*60
            start=int(datetime.fromisoformat(row["first_time"]).timestamp())
            end=int(datetime.fromisoformat(row["last_time"]).timestamp())+step
            if start%step or end%step:
                print(provider,row["timeframe"],"UNALIGNED_UTC_ARCHIVE")
                continue
            feed=FrozenFeed(source=provider+"_SAVED",symbol=row["symbol"],timeframe=row["timeframe"],
                feed_id=row["feed_id"],price_basis=row["price_basis"],start=start,end=end,
                partitions=[{"id":row["id"],"sha256":row["sha256"]}],calendar={"kind":"continuous"})
            outcome=assess(feed,end)
            print(provider,row["timeframe"],outcome["status"],json.dumps({
                mode:{"eligible":value["eligible_candles"],"missing":value["missing_slots"],
                      "blocking_reason":value["blocking_reason"]}
                for mode,value in outcome["modes"].items()},sort_keys=True))


if __name__=="__main__":
    main()
