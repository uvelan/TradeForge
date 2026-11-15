"""Fixed-point, defensive position sizing. Not broker order authorization."""
from decimal import Decimal, ROUND_DOWN
from .schemas import TradePlan

PIP_SIZE = {'EURUSD':Decimal('0.0001'),'GBPUSD':Decimal('0.0001'),'USDJPY':Decimal('0.01'),'XAUUSD':Decimal('0.1')}

def evaluate(plan: TradePlan) -> dict:
    point = PIP_SIZE[plan.symbol]
    distance = abs(plan.entry-plan.stop)
    pips = distance/point
    dollar_risk = plan.equity_usd*plan.risk_percent/Decimal('100')
    theoretical_lots = dollar_risk/(pips*plan.pip_value_per_lot_usd)
    # Illustrative default lot step; broker's min/max/step must be verified before actual trading.
    rounded_lots = theoretical_lots.quantize(Decimal('0.01'), rounding=ROUND_DOWN)
    if rounded_lots < 0: rounded_lots=Decimal(0)
    actual_planned = rounded_lots*pips*plan.pip_value_per_lot_usd
    reward=abs(plan.target-plan.entry)
    rr=reward/distance
    return {
      'pip_size':str(point), 'stop_pips':str(pips.quantize(Decimal('0.01'))),
      'maximum_planned_loss_usd':str(dollar_risk.quantize(Decimal('0.01'))),
      'theoretical_lots':str(theoretical_lots.quantize(Decimal('0.0001'))),
      'illustrative_lots':str(rounded_lots),
      'illustrative_risk_usd':str(actual_planned.quantize(Decimal('0.01'))),
      'reward_risk':str(rr.quantize(Decimal('0.01'))),
      'valid_for_live_execution':False,
      'limitations':['Pip value is user-supplied, not independently broker verified',
          'No spread, slippage, commission, conversion, margin, or broker lot-spec validation',
          'XAUUSD pip-size convention is illustrative and must be overridden by broker specifications']
    }
