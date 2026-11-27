# November MT5 audit capacity on Windows

The product audit processed 1,121,792 observations in **610 ms warm** (1.84 million observations/s). The independent verifier took **682 ms warm** (1.65 million/s). The product audit and independent verifier agreed on all 720 hourly records and the start and end of each of the 45 open-session gaps. Both retained the decision **REJECTED**. [Raw trials and parity receipt](audit-samples.json)

## Wall time and process resources

| Workload | Median wall (ms) | p95 wall (ms) | Observations/s | Peak working set (MiB) | Median read (MiB) | Median write (MiB) |
|---|---:|---:|---:|---:|---:|---:|
| Independent verifier, fresh interpreter | 751.6 | 760.2 | 1,492,548 | 103.32 | 66.65 | 0 |
| Product audit, fresh interpreter | 794.0 | 802.8 | 1,412,892 | 123.09 | 68.65 | 0 |
| Independent verifier, warm interpreter | 681.6 | 697.8 | 1,645,903 | 103.46 | 64.21 | 0 |
| Product audit, warm interpreter | 609.7 | 646.2 | 1,839,804 | 123.05 | 64.21 | 0 |

Each cell used three warm-ups followed by seven measured complete audits. The fresh-interpreter timer starts immediately before importing the audit entry module and stops when its first call returns. It excludes launching Python, loading the benchmark harness, reading the baseline evidence and serializing the output. Warm trials repeat the audit in a single interpreter after three complete passes. Both implementations reopen and authenticate the manifest and all daily files on every pass. The untimed parity preflight reads the complete dataset before timing. Windows manages the filesystem cache throughout, so physical cold-disk latency is unmeasured.

p95 uses nearest rank, which selects the largest of seven samples. Throughput divides observed rows by median wall time. Peak is the largest Windows process-lifetime `PeakWorkingSetSize` through a measured trial. Warm trials share that high-water mark with earlier passes. Byte and operation counts use deltas from `GetProcessIoCounters` over each worker's measurement, including first application imports on fresh workers. These are logical process transfers. Physical disk reads and system-wide memory use were outside the measurement. Both warm paths issued 62 read operations and transferred 67,324,814 bytes per pass. [Raw counters and timings](audit-samples.json)

## Where each audit spends time

| Stage, median warm milliseconds | Independent verifier | Product audit |
|---|---:|---:|
| Read capture bytes and check file bounds where applicable | 29.8 | 337.1 |
| SHA-256 constructor on input bytes | 112.4 | 120.3 |
| Load NumPy arrays | 22.0 | 26.0 |
| Remaining daily decode and validation | 35.9 | 52.0 |
| Group observations by hour and prepare receipt metadata | 376.1 | Included below |
| Apply calendar, classify hours, find gaps and assemble report | 108.6 | 55.6 |

The verifier's largest stage was grouping the observations by hour (55% of its instrumented median wall time). That stage materializes daily row indices and Python integers for each hour. The product's largest stage was the bounded file-read operation (57%). Its `verified_bytes` function calls `stream.read(size_limit + 1)` for the manifest and daily files. The seven product read-stage samples ranged from 335 to 346 ms. This stage includes opening the file, buffering, checking the bound and other wrapper costs. The counters cannot distinguish those costs from storage latency. The measurements identify the stage where further investigation would start. This run made no product optimization. [Verifier implementation](../../../scripts/verify_nov25_windows.py) · [Product implementation](../../../scripts/month_mt5_audit.py)

Stage times come from separate warmed workers that use three warm-ups and seven measured trials. Coarse timers surround the original daily functions, NumPy loads and SHA-256 calls. For the verifier, a marker separates the input grouping loop from the calendar loop. For the product, the file-read stage uses the bounded-read function time less SHA construction time. Small receipt and digest-finalization costs remain in the residuals. Per-trial stages sum to the audit time. Medians of individual stages need not sum to the median total.

## Acceptance and hardware

The manifest pin was `5431d15fa8a24b63b5f5b1f62241caa52110049ab8c87c8d1f7f5783d84d606c`. The thirty original daily arrays occupied 67,315,200 bytes. All daily hashes were verified. The baseline contains 472 observed open hours, ten missing open hours, 230 scheduled closures with no rows, and **12,071 observations in eight closed hours**. The ordered hourly receipt digest was `31943d30975d5218d2be7364f4b3f4b77042827e1acb3574196d9c047d4b238f`. The ordered gap-pair digest was `a58b7912b83c1fd8df88569fcde1107e00ae260cc0fdfc60f0192668ee95cbc2`. The preflight compares hourly fields and exact gap pairs between implementations. Every timed result is checked against the committed reference after the timer stops. [Merged baseline evidence](../nov25-acceptance/independent-windows-replay.json)

The collection host ran Windows 11 build 26200 on an Intel Core i7-9700K, eight physical cores and eight logical processors, with 17,092,456,448 bytes of physical memory. The capture was on a SATA ST2000DM005-2CW102 volume formatted as NTFS. The runtime was 64-bit CPython 3.13.14 with NumPy 2.5.4. All runs used the same host, interpreter, capture, filesystem and source. The audit ran serially in its reserved host timing window. Background Windows activity was uncontrolled.

## Reproduce

Run from a clean tracked worktree containing this evidence and the unchanged audit sources from merge `7d26bbcbd475c9b0550e0f5ac3a8ab8acf505c59`. Supply the Windows project interpreter with the locked dependencies and the absolute path to the original capture. The script uses private temporary storage for the product and verifies the tracked audit sources match that merge.

```powershell
& ..\TradeForge\.venv\Scripts\python.exe docs/implementation/TF-R01/benchmark_mt5_audit.py ..\TradeForge\storage\nov25\mt5\capture.json --output storage/r01/audit-samples.json
```

The output contains all warm-up and measured trials, per-trial I/O operation and transfer counts, process memory, source and receipt digests, classifications, verdict and timings. The retained arrays remain in local storage. [Benchmark command source](benchmark_mt5_audit.py)

The focused MT5 tests passed on this checkout (12 passed, 16 deselected in 8.88 s).
