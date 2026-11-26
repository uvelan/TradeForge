import type {IChartApi, ISeriesApi, UTCTimestamp} from 'lightweight-charts';
import type {Candle} from './types';

export type LinkOptions = {crosshair:boolean;range:boolean;zoom:boolean};
type Member = {chart:IChartApi;series:ISeriesApi<'Candlestick'>;bars:()=>Map<number,Candle>;missing:(time?:number)=>void;linkedTime?:(time?:number)=>void};

export class ChartLink {
  options:LinkOptions={crosshair:false,range:false,zoom:false};
  private members=new Map<string,Member>();
  private driver?:string;
  private frame=0;
  private pointer?:{source:string; member:Member; time?:number};
  private cancelPointer(){
    if(this.frame)cancelAnimationFrame(this.frame);
    this.frame=0;
    this.pointer=undefined;
  }
  private clearPeers(source:string){
    this.members.forEach(({chart,missing,linkedTime},id)=>{
      if(id!==source){chart.clearCrosshairPosition();missing();linkedTime?.();}
    });
  }
  private release(id:string,member:Member){
    if(this.members.get(id)!==member)return;
    this.members.delete(id);
    if(this.driver!==id&&this.pointer?.member!==member)return;
    if(this.driver===id)this.driver=undefined;
    const pending=this.pointer?.member===member;
    if(pending)this.cancelPointer();
    if(pending||this.options.crosshair)this.clearPeers(id);
  }
  register(id:string,member:Member) {
    const previous=this.members.get(id);
    if(previous&&previous!==member)this.release(id,previous);
    this.members.set(id,member);
    return ()=>this.release(id,member);
  }
  activate(id:string){
    if(!this.members.has(id))return;
    if(this.pointer&&this.pointer.source!==id){
      this.cancelPointer();
      this.clearPeers(id);
    }
    this.driver=id;
  }
  cursor(source:string,time?:number){
    const member=this.members.get(source);
    if(!member||!this.options.crosshair||this.driver!==source)return;
    // The pane ID may be re-used on a source/timeframe remount.
    this.pointer={source,member,time};
    if(this.frame)return;
    this.frame=requestAnimationFrame(()=>{
      this.frame=0;
      const pointer=this.pointer;
      this.pointer=undefined;
      if(!pointer||!this.options.crosshair||this.members.get(pointer.source)!==pointer.member)return;
      this.members.forEach(({chart,series,bars,missing,linkedTime},id)=>{
        if(id===pointer.source)return;
        const candle=pointer.time===undefined?undefined:bars().get(pointer.time);
        linkedTime?.(candle?.time);
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
