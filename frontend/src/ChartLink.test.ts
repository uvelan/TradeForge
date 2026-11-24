import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChartLink } from "./ChartLink";
import type { IChartApi, ISeriesApi } from "lightweight-charts";
import type { Candle } from "./types";

function pane(times: number[]) {
  const setCrosshairPosition = vi.fn();
  const clearCrosshairPosition = vi.fn();
  const missing = vi.fn();
  const candles = times.map(time=>[time,{time,open:10,high:11,low:9,close:10} satisfies Candle] as const);
  const series = {} as ISeriesApi<'Candlestick'>;
  return {
    member:{chart:{setCrosshairPosition,clearCrosshairPosition} as unknown as IChartApi,series,
      bars:()=>new Map(candles),missing},
    setCrosshairPosition,clearCrosshairPosition,missing,
  };
}

describe("linked chart cursor lifecycle",()=>{
  let callbacks: Map<number,FrameRequestCallback>;
  let link: ChartLink;
  beforeEach(()=>{
    let next=0;
    callbacks=new Map();
    vi.stubGlobal("requestAnimationFrame",(fn:FrameRequestCallback)=>{callbacks.set(++next,fn);return next;});
    vi.stubGlobal("cancelAnimationFrame",(id:number)=>callbacks.delete(id));
    link=new ChartLink();
    link.options.crosshair=true;
  });
  afterEach(()=>vi.unstubAllGlobals());
  function paint(){
    const jobs=[...callbacks.values()];
    callbacks.clear();
    jobs.forEach(job=>job(0));
  }

  it("cancels a queued cursor from a departing source before a peer paints",()=>{
    const release=link.register("main",pane([100]).member);
    const peer=pane([100,200]);
    link.register("peer",peer.member);
    link.activate("main");
    link.cursor("main",100);
    release();
    link.register("main",pane([200]).member);
    paint();
    expect(peer.setCrosshairPosition).not.toHaveBeenCalled();
    link.activate("main");
    link.cursor("main",200);
    paint();
    expect(peer.setCrosshairPosition).toHaveBeenCalledWith(10,200,peer.member.series);
  });

  it("clears previous peer markers when a driver is removed, including a queued pointer leave",()=>{
    const release=link.register("main",pane([100,200]).member);
    const peer=pane([100]);
    link.register("peer",peer.member);
    link.activate("main");
    link.cursor("main",100);
    paint();
    expect(peer.setCrosshairPosition).toHaveBeenCalledOnce();
    link.cursor("main",200);
    paint();
    expect(peer.missing).toHaveBeenLastCalledWith(200);
    link.clearCursor("main");
    release();
    expect(peer.clearCrosshairPosition).toHaveBeenCalledTimes(2);
    expect(peer.missing).toHaveBeenLastCalledWith();
    expect(callbacks.size).toBe(0);
  });

  it("ignores outdated registration cleanup after the replacement starts driving",()=>{
    const oldRelease=link.register("main",pane([100]).member);
    const peer=pane([100,200]);
    link.register("peer",peer.member);
    link.activate("main");
    link.cursor("main",100);
    link.register("main",pane([200]).member);
    link.activate("main");
    oldRelease();
    link.cursor("main",200);
    paint();
    expect(peer.setCrosshairPosition).toHaveBeenCalledExactlyOnceWith(10,200,peer.member.series);
  });

  it("does not alter peer crosshairs when linking is disabled",()=>{
    link.options.crosshair=false;
    const release=link.register("main",pane([100]).member);
    const peer=pane([100]);
    link.register("peer",peer.member);
    link.activate("main");
    link.cursor("main",100);
    release();
    paint();
    expect(peer.setCrosshairPosition).not.toHaveBeenCalled();
    expect(peer.clearCrosshairPosition).not.toHaveBeenCalled();
  });

  it("does not paint the old pane cursor after focus moves before the next frame",()=>{
    const guide=pane([100,200]);
    link.register("guide",guide.member);
    link.register("first",pane([100]).member);
    link.register("second",pane([200]).member);
    link.activate("first");
    link.cursor("first",100);
    link.activate("second");
    paint();
    expect(guide.setCrosshairPosition).not.toHaveBeenCalled();
    link.cursor("second",200);
    paint();
    expect(guide.setCrosshairPosition).toHaveBeenCalledExactlyOnceWith(10,200,guide.member.series);
  });
});
