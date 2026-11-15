"""Research-only next-bar breakout simulation. OHLC ambiguity is never guessed."""
from __future__ import annotations


def breakout_backtest(candles:list[dict],target_r:float=2.0,max_bars:int=12):
    if not 1<=target_r<=5 or not 1<=max_bars<=100:raise ValueError('Invalid backtest parameters')
    if len(candles)<25: raise ValueError('At least 25 complete candles required')
    outcomes=[]; ambiguity=0; i=2
    while i < len(candles)-max_bars-1:
        previous=candles[i-1];signal=candles[i]
        # Signal is a completed candle. Entry uses the NEXT bar's open.
        side='BUY' if signal['close']>previous['high'] else 'SELL' if signal['close']<previous['low'] else None
        if not side:i+=1;continue
        entry=float(candles[i+1]['open']);stop=float(min(previous['low'],signal['low']) if side=='BUY' else max(previous['high'],signal['high']))
        risk=abs(entry-stop)
        if risk<=0 or (side=='BUY' and stop>=entry) or (side=='SELL' and stop<=entry):i+=1;continue
        target=entry+(risk*target_r if side=='BUY' else -risk*target_r)
        end=min(len(candles),i+max_bars+2)
        result=None;exit_i=end-1
        for j in range(i+1,end):
            bar=candles[j];low=float(bar['low']);high=float(bar['high'])
            hit_stop=low<=stop if side=='BUY' else high>=stop
            hit_target=high>=target if side=='BUY' else low<=target
            if hit_stop and hit_target:
                result='AMBIGUOUS';ambiguity+=1;exit_i=j;break
            if hit_stop:result=-1.0;exit_i=j;break
            if hit_target:result=target_r;exit_i=j;break
        if result is None:
            last=float(candles[end-1]['close']);result=(last-entry)/risk*(1 if side=='BUY' else -1)
            exit_i=end-1
        if result!='AMBIGUOUS':
            outcomes.append({'entry_time':candles[i+1]['time'],'exit_time':candles[exit_i]['time'],
                'side':side,'entry':entry,'stop':stop,'target':round(target,7),'result_r':round(float(result),4)})
        i=exit_i+1  # No overlapping positions in this laboratory model.
    vals=[x['result_r'] for x in outcomes]
    wins=sum(1 for x in vals if x>0)
    eligible = len(vals) + ambiguity
    total = sum(vals)
    return {'strategy':'Previous bar breakout, next-bar entry, fixed RR, conservative OHLC ambiguity',
      'trades':outcomes,'count':len(vals),'ambiguous_excluded':ambiguity,
      'eligible_signals':eligible,
      'ambiguous_rate':round(100*ambiguity/eligible,2) if eligible else None,
      'win_rate':round(wins/eligible*100,2) if eligible and not ambiguity else None,
      'expectancy_r':round(total/eligible,4) if eligible and not ambiguity else None,
      'expectancy_bounds_r':{
          'worst':round((total-ambiguity)/eligible,4),
          'best':round((total+ambiguity*target_r)/eligible,4),
      } if eligible else None,
      'total_r':round(total,4) if not ambiguity else None,
      'data_note':'Uses supplied OHLC with no spread/slippage/commission; not execution-grade. DEMO data is synthetic when source=DEMO.',
      'limitations':['Tick replay is required to resolve ambiguous stop/target ordering',
                     'No spread, swap, broker commissions, or slippage',
                     'Illustrative mechanical strategy, no demonstrated out-of-sample edge']}
