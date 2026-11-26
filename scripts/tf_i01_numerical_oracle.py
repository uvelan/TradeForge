"""Independently check the six added OHLC indicators without runtime dependencies.

Run from the repository root with `python -m scripts.tf_i01_numerical_oracle`.
Use --talib only in a temporary development environment with NumPy and TA-Lib.
"""
import argparse
from fractions import Fraction as F
import json
from math import isclose


ABS = 1e-10
REL = 1e-12


def average(xs):
    return sum(xs) / len(xs)


def smooth(xs, p, a):
    """Closed-form convolution of the seed plus timestamp-weighted observations."""
    seed, remain = average(xs[:p]), 1-a
    return [None]*(p-1) + [
        seed*remain**(i-p+1) + sum(a*xs[j]*remain**(i-j) for j in range(p,i+1))
        for i in range(p-1,len(xs))
    ]


def range_pct(xs, x):
    lo, hi = min(xs),max(xs)
    return F(50) if lo==hi else 100*(x-lo)/(hi-lo)


def stochastic(xs, bounds, p, start=0):
    k = [range_pct([
        value for bar in bounds[i-p+1:i+1] for value in bar
    ],xs[i]) for i in range(p-1,len(xs))]
    first = start+p-1
    return {"%K":[None]*first+k,
            "%D":[None]*(first+2)+[average(k[i-2:i+1]) for i in range(2,len(k))]}


def rsi_reference(close,p):
    diff = [b-a for a,b in zip(close,close[1:])]
    gain, loss = (smooth([max(F(0),sign*d) for d in diff],p,F(1,p))[p-1:] for sign in (1,-1))
    return [100*g/(g+l) if g+l else F(50) for g,l in zip(gain,loss)]


def exact_reference(prices,p):
    high,low,close = map(list,zip(*prices))
    span = [high[0]-low[0]] + [max(high[i],close[i-1])-min(low[i],close[i-1])
                               for i in range(1,len(close))]
    alpha = F(1,p)
    atr = smooth(span,p,alpha)
    rise = [high[i+1]-high[i] for i in range(len(close)-1)]
    fall = [low[i]-low[i+1] for i in range(len(close)-1)]
    plus = [x if x>0 and x>y else F(0) for x,y in zip(rise,fall)]
    minus = [y if y>0 and y>x else F(0) for x,y in zip(rise,fall)]
    raw_strength = [smooth(v,p,alpha)[p-1:] for v in (plus,minus,span[1:])]
    di = [[100*num/den if den else F(0) for num,den in zip(v,raw_strength[2])]
          for v in raw_strength[:2]]
    dx = [100*abs(x-y)/(x+y) if x+y else F(0) for x,y in zip(*di)]
    out = {"adx":{"ADX":[None]*p+smooth(dx,p,alpha),"+DI":[None]*p+di[0],"-DI":[None]*p+di[1]}}
    out["stochastic"] = stochastic(close,list(zip(low,high)),p)
    rsi = rsi_reference(close,p)
    out["stochrsi"] = stochastic(rsi,[(x,x) for x in rsi],p,start=p)
    typ = [sum(row)/3 for row in prices]
    cci = [None]*(p-1)
    for i in range(p-1,len(close)):
        window = typ[i-p+1:i+1]
        mean = average(window)
        deviation = average([abs(x-mean) for x in window])
        cci.append((typ[i]-mean)/(F(3,200)*deviation) if deviation else F(0))
    out["cci"] = {"CCI":cci}
    center = smooth(close,p,F(2,p+1))
    out["keltner"] = {
        "Basis":center,
        "Upper":[None if x is None else x+2*a for x,a in zip(center,atr)],
        "Lower":[None if x is None else x-2*a for x,a in zip(center,atr)],
    }
    return out


def trend_golden():
    # Period 2. Closes [10,12,14,16,20,22,10]; high/low = close +/- 1.
    # Wilder ATR begins [5/2,11/4,23/8,63/16,111/32,527/64].
    # Seed upper 39/2; no up flip until close 20; lower ratchets at 22;
    # Bar six upper resets to 22 + 3*111/32. Bar seven basic upper is higher,
    # and the previous close is below the final upper, so the down flip
    # selects the retained 1037/32 rather than the current basic band.
    return [None,F(39,2),F(39,2),F(39,2),F(131,16),F(371,32),F(1037,32)]


def check(name,got,expected):
    want = [(i*60,float(x)) for i,x in enumerate(expected) if x is not None]
    assert [p["time"] for p in got]==[time for time,_ in want], (name, "unexpected timestamp sequence")
    comparisons = [(t,p["value"],v) for p,(t,v) in zip(got,want,strict=True)]
    for t,a,b in comparisons:
        assert isclose(a,b,rel_tol=REL,abs_tol=ABS), (name,t,a,b)
    return {"first_valid_bar":want[0][0]//60+1 if want else None,
            "count":len(want),"max_abs_error":max((abs(a-b) for _,a,b in comparisons),default=0.0)}


def bars(prices):
    from research_backend.app.indicators import Bar
    return [Bar(time=60*i,open=float(c),high=float(h),low=float(l),close=float(c))
            for i,(h,l,c) in enumerate(prices)]


def library_oracle(prices, period, actual):
    import numpy as np
    import talib
    high,low,close = np.array(prices,dtype=float).T
    fastk,fastd = talib.STOCHF(high,low,close,fastk_period=period,fastd_period=3,fastd_matype=0)
    rsi_k,rsi_d = talib.STOCHRSI(close,timeperiod=period,fastk_period=period,fastd_period=3,fastd_matype=0)
    matches = {"cci/CCI":talib.CCI(high,low,close,timeperiod=period),
               "stochastic/%K":fastk,"stochastic/%D":fastd,"stochrsi/%K":rsi_k,"stochrsi/%D":rsi_d,
               "keltner/Basis":talib.EMA(close,timeperiod=period)}
    out = {}
    rsi = rsi_reference([row[2] for row in prices],period)
    flat_k = {i+period for i in range(period-1,len(rsi))
              if len(set(rsi[i-period+1:i+1]))==1}
    flat_d = {i+d for i in flat_k for d in range(3)}
    differences = {}
    for key,expected in matches.items():
        name,label = key.split("/")
        omitted = (flat_k if label=="%K" else flat_d) if name=="stochrsi" else set()
        points = {p["time"]//60:p["value"] for p in actual[name][label]}
        skipped = [i for i in omitted if i in points and np.isfinite(expected[i])]
        if skipped:
            differences[key] = {"count":len(skipped),
                                "max_abs_error":max(abs(points[i]-expected[i]) for i in skipped)}
        obs = [p for p in actual[name][label]
               if p["time"]//60 not in omitted and np.isfinite(expected[p["time"]//60])]
        reference = [None if i in omitted or not np.isfinite(v) else v for i,v in enumerate(expected)]
        comparison = check(key,obs,reference)
        comparison["first_compared_bar"] = comparison.pop("first_valid_bar")
        comparison["first_reference_bar"] = int(np.flatnonzero(np.isfinite(expected))[0])+1
        out[key] = comparison
    diagnostic = {
        "adx/ADX":talib.ADX(high,low,close,timeperiod=period),
        "adx/+DI":talib.PLUS_DI(high,low,close,timeperiod=period),
        "adx/-DI":talib.MINUS_DI(high,low,close,timeperiod=period),
        "keltner/Upper":matches["keltner/Basis"]+2*talib.ATR(high,low,close,timeperiod=period),
        "keltner/Lower":matches["keltner/Basis"]-2*talib.ATR(high,low,close,timeperiod=period),
    }
    seed_differences = {}
    for key,ref in diagnostic.items():
        name,label = key.split("/")
        delta = [abs(p["value"]-ref[p["time"]//60]) for p in actual[name][label]
                 if np.isfinite(ref[p["time"]//60])]
        seed_differences[key] = {"max_abs_error":max(delta),
                                 "first_valid_bar":int(np.flatnonzero(np.isfinite(ref))[0])+1,
                                 "count":len(delta)}
    return {"versions":{"talib":talib.__version__,"libta":talib.__ta_version__.decode(),"numpy":np.__version__},
            "matching_conventions":out,"different_seed_convention":seed_differences,
            "different_flat_window_convention":differences}


def run(external=False):
    from research_backend.app.indicators import calculate
    # A deterministic non-monotone, non-flat OHLC path with positive prices,
    # gap bars and many directional ties, in exact quarters.
    prices = [(c+F((i*7)%11+1,4),c-F((i*13)%7+1,4),c)
              for i in range(72) for c in [F(400+(i*13)%71+(i//7)*5,4)]]
    out = {}
    for period in (3,5,10,14,20):
        expected = exact_reference(prices,period)
        actual = {name:calculate(bars(prices),name,period)["series"] for name in expected}
        result = {name:{label:check(f"{name}/{label}",actual[name][label],vector)
                        for label,vector in series.items()} for name,series in expected.items()}
        # Independent exact flat vector. All prices are precisely representable.
        flat = [(F(64),F(64),F(64))]*48
        flat_expected = exact_reference(flat,period)
        for name,series in flat_expected.items():
            observed = calculate(bars(flat),name,period)["series"]
            for label,v in series.items():
                check(f"flat {name}/{label}",observed[label],v)
        flat_trend = calculate(bars(flat),"supertrend",period)["series"]["SuperTrend"]
        result["flat SuperTrend"] = check("flat SuperTrend",flat_trend,[None]*(period-1)+[F(64)]*(49-period))
        if external:
            result["external"] = library_oracle(prices,period,actual)
        out[str(period)] = result
    # Boundary periods use exact constant outcomes to avoid quadratic
    # rational work at the maximum. Nonflat and external cases use the five
    # periods above; this check does not claim all accepted periods are sampled.
    for period in (2,200):
        n = 2*period+4
        flat = bars([(F(64),F(64),F(64))]*n)
        series = {
            "adx":{"ADX":(2*period,0),"+DI":(period+1,0),"-DI":(period+1,0)},
            "stochastic":{"%K":(period,50),"%D":(period+2,50)},
            "stochrsi":{"%K":(2*period,50),"%D":(2*period+2,50)},
            "cci":{"CCI":(period,0)},
            "keltner":{key:(period,64) for key in ("Basis","Upper","Lower")},
            "supertrend":{"SuperTrend":(period,64)},
        }
        out[f"flat boundary (p={period})"] = {}
        for name,spec in series.items():
            got = calculate(flat,name,period)["series"]
            out[f"flat boundary (p={period})"][name] = {
                label:check(f"flat boundary {name}/{label}",got[label],
                            [None]*(first-1)+[F(value)]*(n-first+1))
                for label,(first,value) in spec.items()
            }
    path = [(F(c+1),F(c-1),F(c)) for c in (10,12,14,16,20,22,10)]
    out["SuperTrend flips (p=2)"] = check("SuperTrend flips",calculate(bars(path),"supertrend",2)["series"]["SuperTrend"],
                                           trend_golden())
    # Seed at upper 16. A close of 16 touches without flipping; 17 flips
    # to lower 9. A close of 9 holds; 8 flips to upper 139/8.
    touches = [(F(h),F(l),F(c)) for h,l,c in ((11,9,10),(11,9,10),(16,15,16),(17,16,17),(10,9,9),(8,8,8))]
    out["SuperTrend touches (p=2)"] = check("SuperTrend touches",
        calculate(bars(touches),"supertrend",2)["series"]["SuperTrend"],
        [None,F(16),F(16),F(9),F(9),F(139,8)])
    return {"abs_tolerance":ABS,"rel_tolerance":REL,"results":out}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--talib",action="store_true",help="optional comparison in a throwaway NumPy/TA-Lib environment")
    print(json.dumps(run(parser.parse_args().talib),indent=2,sort_keys=True))
