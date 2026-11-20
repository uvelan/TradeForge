"""Local single-worker supervisor with an externally enforced process deadline."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, time
from uuid import uuid4

from .analysis_evidence import ResourceLimit
from .config import ROOT
from .db import one
from .historical_strategy_engine import run_chunks
from .historical_strategy_inputs import HARD_SECONDS, MEMORY_BYTES, RUN_VERSION, verified_evidence
from .historical_strategy_store import LeaseLost, claim, finish, heartbeat, latest_checkpoint, publish, status
from .strategy_definitions import StrategyDefinitionV2
from .strategy_evaluation_sources import EVALUATOR_VERSION
from .strategy_evidence import fingerprint


def child(run_id, owner):
    try:
        entry = one("SELECT payload FROM historical_strategy_runs WHERE id=?",(run_id,))
        if not entry:
            raise ValueError("Historical run not found")
        run = json.loads(entry["payload"])
        identity = run["identity"]
        if (identity["historical_version"]!=RUN_VERSION or identity["evaluator_version"]!=EVALUATOR_VERSION
                or run["run_id"]!="HR-"+fingerprint(identity)
                or identity["frozen_selection"]!=fingerprint(run["selection"])):
            raise ValueError("Incompatible or corrupt historical run identity")

        next_check = 0.0

        def check():
            nonlocal next_check
            now = monotonic()
            if now < next_check:
                return
            record = status(run_id)
            if (not record or record["state"]!="RUNNING" or record["owner"]!=owner
                    or record["lease_until"]<=time()):
                raise LeaseLost()
            next_check = now + 0.15

        definition = StrategyDefinitionV2.model_validate(run["definition"])
        check()
        evidence = verified_evidence(definition,run,check)
        run_chunks(run,evidence,definition,lambda *args:publish(run_id,owner,*args),
                   saved=latest_checkpoint(run_id),check=check)
    except LeaseLost:
        finish(run_id,owner,"CANCELLED","Publication fence revoked")
    except (ResourceLimit,MemoryError) as error:
        finish(run_id,owner,"RESOURCE_LIMIT_EXCEEDED",error)
    except Exception as error:
        finish(run_id,owner,"FAILED",f"{type(error).__name__}: {error}")


def working_set(pid):
    if sys.platform=="win32":
        import ctypes
        from ctypes import wintypes

        class ProcessMemory(ctypes.Structure):
            _fields_ = [("cb",wintypes.DWORD),("faults",wintypes.DWORD),
                        ("peak",ctypes.c_size_t),("current",ctypes.c_size_t),
                        ("remaining",ctypes.c_size_t*6)]

        kernel = ctypes.WinDLL("kernel32")
        kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000,False,pid)
        if not handle:
            return None
        try:
            read = ctypes.WinDLL("psapi").GetProcessMemoryInfo
            read.argtypes = [wintypes.HANDLE,ctypes.POINTER(ProcessMemory),wintypes.DWORD]
            info = ProcessMemory()
            info.cb = ctypes.sizeof(info)
            return info.peak if read(handle,ctypes.byref(info),info.cb) else None
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        try:
            with Path(f"/proc/{pid}/status").open() as stream:
                return next(int(row.split()[1])*1024 for row in stream if row.startswith("VmHWM:"))
        except (OSError,StopIteration):
            return None
    return None


async def supervise(stop):
    proc = None
    run_id = None
    owner = None
    try:
        while not stop.is_set():
            if proc is None:
                owner = uuid4().hex
                run_id = await asyncio.to_thread(claim,owner)
                if run_id:
                    try:
                        proc = subprocess.Popen([sys.executable,"-m",__name__,run_id,owner],
                            cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
                        started = monotonic()
                    except OSError as error:
                        await asyncio.to_thread(finish,run_id,owner,"FAILED",error)
            elif proc.poll() is not None:
                await asyncio.to_thread(finish,run_id,owner,"FAILED",f"Worker exited with code {proc.returncode}")
                proc = None
            else:
                elapsed = monotonic()-started
                limit = working_set(proc.pid)
                alive = await asyncio.to_thread(heartbeat,run_id,owner)
                if not alive or elapsed>=HARD_SECONDS or limit is not None and limit>MEMORY_BYTES:
                    proc.kill()
                    await asyncio.to_thread(proc.wait)
                    reason = ("Worker lease or cancellation" if not alive else
                              "External process deadline" if elapsed>=HARD_SECONDS else "Sampled working-set limit")
                    await asyncio.to_thread(finish,run_id,owner,
                                            "FAILED" if not alive else "RESOURCE_LIMIT_EXCEEDED",reason)
                    proc = None
            try:
                await asyncio.wait_for(stop.wait(),timeout=0.5 if proc else 1.0)
            except TimeoutError:
                pass
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            await asyncio.to_thread(proc.wait)
            await asyncio.to_thread(finish,run_id,owner,"FAILED","Local service stopped; resume from the committed checkpoint")


if __name__=="__main__":
    child(*sys.argv[1:])
