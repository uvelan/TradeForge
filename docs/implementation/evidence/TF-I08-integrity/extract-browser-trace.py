"""Extract relevant browser trace records without retaining video or response bodies."""
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from zipfile import ZipFile

for path in (Path(arg) for arg in sys.argv[1:]):
    print(path.parent.name)
    with ZipFile(path) as archive:
        for name in archive.namelist():
            if name.endswith(".network"):
                for line in archive.read(name).splitlines():
                    row=json.loads(line).get("snapshot",{})
                    request=row.get("request",{})
                    endpoint=urlsplit(request.get("url","")).path
                    if not endpoint.endswith(("/drawings","/indicators/evaluate","/replay/sessions","/checkpoint")):
                        continue
                    if request.get("method")!="POST":
                        continue
                    payload=request.get("postData",{}).get("text")
                    if payload and endpoint.endswith("/checkpoint"):
                        payload=json.loads(payload)
                    else:
                        payload=None
                    print("  HTTP",endpoint,row.get("response",{}).get("status"),payload)
            elif name.endswith("-trace.trace"):
                for line in archive.read(name).splitlines():
                    row=json.loads(line)
                    params=row.get("params",{})
                    selector=params.get("selector","")
                    if row.get("method")=="mouseClick":
                        print("  pointer",params,"in a 1366x768 viewport")
                    if row.get("type")=="log" and "intercepts pointer events" in row.get("message",""):
                        print("  actionability",row["message"])
                        continue
                    if any(text in selector for text in ("chart-input","Replay timeline","Select drawing","Bookmark candle","Historical replay","Save replay position")) and row.get("method") in ("click","expect"):
                        print(" ",row["method"],selector,params.get("expectedValue",""))
