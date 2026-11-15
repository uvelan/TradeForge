import type {IChartApi, ISeriesApi, UTCTimestamp} from 'lightweight-charts';
import type {Candle} from './types';

export type LinkOptions = {crosshair:boolean;range:boolean;zoom:boolean};
type Member = {chart:IChartApi;series:ISeriesApi<'Candlestick'>;bars:()=>Map<number,Candle>;missing:(time?:number)=>void};

export class ChartLink {
  options:LinkOptions={crosshair:false,range:false,zoom:false};
  private members=new Map<string,Member>();
  private driver?:string;
  private frame=0;
  private pointer?:{source:string; time?:number};
  register(id:string,member:Member) {
    this.members.set(id,member);
    return ()=>{
      if(this.members.get(id)===member)this.members.delete(id);
      if(this.driver===id)this.driver=undefined;
      if(!this.members.size&&this.frame){cancelAnimationFrame(this.frame);this.frame=0;this.pointer=undefined;}
    };
  }
  activate(id:string){this.driver=id;}
  cursor(source:string,time?:number){
    if(!this.options.crosshair||this.driver!==source)return;
    this.pointer={source,time};
    if(this.frame)return;
    this.frame=requestAnimationFrame(()=>{
      this.frame=0;
      const pointer=this.pointer;
      if(!pointer||!this.options.crosshair)return;
      this.members.forEach(({chart,series,bars,missing},id)=>{
        if(id===pointer.source)return;
        const candle=pointer.time===undefined?undefined:bars().get(pointer.time);
        if(candle){
          missing();
          chart.setCrosshairPosition(candle.close,candle.time as UTCTimestamp,series);
        } else {
          chart.clearCrosshairPosition();
          missing(pointer.time);
        }
      });
    });
  }
  clearCursor(source:string){
    if(this.driver!==source)return;
    this.cursor(source);
    this.driver=undefined;
  }
  viewport(source:string){
    if(this.driver!==source||!this.options.range&&!this.options.zoom)return;
    const origin=this.members.get(source)?.chart.timeScale();
    const range=origin?.getVisibleRange();
    const logical=origin?.getVisibleLogicalRange();
    this.members.forEach(({chart,bars},id)=>{
      if(id===source||!bars().size)return;
      const scale=chart.timeScale();
      const current=scale.getVisibleLogicalRange();
      const currentTime=scale.getVisibleRange();
      if(this.options.range&&range&&current&&currentTime){
        const from=Number(range.from),to=Number(range.to);
        const keys=Array.from(bars().keys());
        if(to<keys[0]||from>keys[keys.length-1])return;
        if(this.options.zoom)scale.setVisibleRange(range);
        else {
          const center=keys.find(time=>time>=(from+to)/2)??keys[keys.length-1];
          const index=scale.timeToIndex(center as UTCTimestamp);
          if(index!==null){const radius=(current.to-current.from)/2;
            scale.setVisibleLogicalRange({from:index-radius,to:index+radius});}
        }
      }else if(this.options.zoom&&logical&&current){
        const center=(current.to+current.from)/2,radius=(logical.to-logical.from)/2;
        scale.setVisibleLogicalRange({from:center-radius,to:center+radius});
      }
    });
  }
}
