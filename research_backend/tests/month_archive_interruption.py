"""Offline provider fixture for the acquisition CLI interruption test."""

import lzma
import os
from pathlib import Path
import runpy
import struct
import subprocess
import sys
from time import sleep


def main():
    mode = sys.argv.pop(1)
    receipt = Path(sys.argv[sys.argv.index("--receipt") + 1])
    calls = 0
    original_open = Path.open
    original_run = subprocess.run
    receipt.with_suffix(f".{mode}.pid").write_text(str(os.getpid()), encoding="ascii")

    def local_curl(command, *args, **kwargs):
        if command[0] != "curl":
            return original_run(command, *args, **kwargs)
        nonlocal calls
        calls += 1
        with receipt.with_suffix(".calls").open("a", encoding="ascii") as log:
            log.write(command[-1] + "\n")
        headers = Path(command[command.index("--dump-header") + 1])
        if mode == "interrupt":
            headers.write_bytes(b"HTTP/1.1 503 Service Unavailable\n")
        elif mode == "resume" and calls == 1:
            headers.write_bytes(b"HTTP/1.1 200 OK\n")
            ticks = struct.pack(">IIIff", 100, 109001, 109000, 1.0, 2.0)
            raw = lzma.compress(ticks, format=lzma.FORMAT_ALONE)
            return subprocess.CompletedProcess(command, 0, raw, b"")
        elif mode == "resume" and calls == 2:
            headers.write_bytes(b"HTTP/1.1 429 Too Many Requests\nRetry-After: 180\n")
        else:
            raise AssertionError("Unexpected provider call")
        return subprocess.CompletedProcess(command, 22, b"", b"")

    class InterruptingReceipt:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __iter__(self):
            return iter(self.stream)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def write(self, data):
            if calls != 3:
                return self.stream.write(data)
            self.stream.write(data[:31])
            self.stream.flush()
            os.fsync(self.stream.fileno())
            receipt.with_suffix(".ready").touch()
            while True:
                sleep(0.1)

    def open_receipt(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        if path == receipt and "a" in mode:
            return InterruptingReceipt(stream)
        return stream

    if mode == "interrupt":
        Path.open = open_receipt
    subprocess.run = local_curl
    runpy.run_module("scripts.month_archive", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
