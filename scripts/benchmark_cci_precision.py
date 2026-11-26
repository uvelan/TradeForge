"""Compare deterministic CCI calculator timings against the reported baseline."""
import ast
from statistics import median
from subprocess import check_output
from time import perf_counter_ns

from research_backend.app import indicators

base = "3ee38870ef86e3896039e28eea595bff8108ce57"
source = check_output(["git", "show", base + ":research_backend/app/indicators.py"], text=True)
function = next(node for node in ast.parse(source).body if getattr(node, "name", "") == "commodity_channel")
previous = vars(indicators).copy()
exec(compile(ast.Module(body=[function], type_ignores=[]), "baseline", "exec"), previous)
baseline = previous["commodity_channel"]
fixed = indicators.commodity_channel

for count, period, case in [(2,2,"minimum"), (2000,20,"regular"), (2000,200,"API caps"),
                            (2000,200,"API caps wide exponents")]:
    data = []
    for index in range(count):
        if case == "API caps wide exponents":
            low = (index%17+1)*1e-300 if index%3==0 else (index%17+1)*1e100
            high, close = low*1.5, low*1.1
        else:
            low = 1.60003 + ((index*37)%113)*0.00001
            high, close = low+0.00005,low+0.00002
        data.append(indicators.Bar(time=index*300,open=close,high=high,low=low,close=close))
    times = {function:[] for function in (baseline, fixed)}
    for i in range(26):
        for func in (baseline, fixed) if i%2==0 else (fixed, baseline):
            start = perf_counter_ns()
            result = func(data, period)
            elapsed = perf_counter_ns()-start
            assert len(result["CCI"]) == count
            if i >= 6:
                times[func].append(elapsed / 1e6)
    print(f"{case} n={count} p={period}: base median {median(times[baseline]):.5f}ms; "
          f"final median {median(times[fixed]):.5f}ms, max {max(times[fixed]):.5f}ms",flush=True)
