import type {IChartApi, ISeriesApi, ISeriesPrimitive, UTCTimestamp} from 'lightweight-charts';
type PriceZone = {c_time:number;remaining_lower:number;remaining_upper:number;
  direction:'BULLISH'|'BEARISH'; kind?:'ob'};

export class FvgLayer implements ISeriesPrimitive {
  private zones: PriceZone[] = [];
  private times: number[] = [];
  private repaint?: () => void;

  constructor(private chart: IChartApi, private series: ISeriesApi<'Candlestick'>) {}

  attached({requestUpdate}: {requestUpdate: () => void}) {
    this.repaint = requestUpdate;
  }

  detached() {
    this.repaint = undefined;
  }

  update(zones: PriceZone[], times: number[]) {
    this.zones = zones;
    this.times = times;
    this.repaint?.();
  }

  paneViews(): ReturnType<NonNullable<ISeriesPrimitive['paneViews']>> {
    return [{
      zOrder: () => 'bottom',
      renderer: () => ({
        draw: target => target.useMediaCoordinateSpace(({context, mediaSize}) => {
          const last = this.times.at(-1);
          if (last === undefined) return;
          const scale = this.chart.timeScale();
          const end = scale.timeToCoordinate(last as UTCTimestamp);
          if (end === null) return;
          for (const zone of this.zones) {
            const start = this.times.find(time => time >= zone.c_time);
            if (start === undefined) continue;
            const x = scale.timeToCoordinate(start as UTCTimestamp);
            const top = this.series.priceToCoordinate(zone.remaining_upper);
            const bottom = this.series.priceToCoordinate(zone.remaining_lower);
            if (x === null || top === null || bottom === null) continue;
            const left = Math.max(0, x - 3);
            const right = Math.min(mediaSize.width, end + 6);
            if (right <= left) continue;
            context.fillStyle = zone.kind === 'ob'
              ? (zone.direction === 'BULLISH' ? 'rgba(96,165,250,0.25)' : 'rgba(251,146,60,0.25)')
              : (zone.direction === 'BULLISH' ? 'rgba(45,212,191,0.23)' : 'rgba(244,114,182,0.23)');
            context.fillRect(left, top, right - left, Math.max(1, bottom - top));
          }
        }),
      }),
    }];
  }
}
