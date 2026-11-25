import type {Candle,Timeframe} from './types';

type Pin={id:string;sha256:string};
type Feed={feed_id?:string;price_basis:string;partitions?:Pin[];dataset_id?:string;sha256?:string};
export function snapshotLane(role:'primary'|'comparison',symbol:string,timeframe:Timeframe,feed:Feed|undefined,candles:Candle[]){
  const partitions=feed?.partitions?.length?feed.partitions:
    feed?.dataset_id&&feed.sha256?[{id:feed.dataset_id,sha256:feed.sha256}]:undefined;
  if(!feed?.feed_id||!partitions||!candles.length)throw new Error('Plotted archive references unavailable for '+symbol);
  return {role,symbol,timeframe,feed_id:feed.feed_id,price_basis:feed.price_basis,partitions,
    bars:candles.map(({time,open,high,low,close})=>({time,open,high,low,close}))};
}
export type SnapshotLane=ReturnType<typeof snapshotLane>;
