import {describe,expect,it} from 'vitest';
import {hourlyCoverageTone} from './ForexCoverage';
import {closedCandles,visibleAt} from './ReplayControls';
import {sessionBounds} from './SessionIntelligence';
import {parsePreset} from './WorkspacePresets';
import type {CandleResponse} from './types';

const at=(value:string)=>Date.parse(value)/1000;
describe('research workspace',()=>{
  it('freezes only closed primary bars and gates the peer by its own availability',()=>{
    const closed={time:0,open:10,high:12,low:9,close:11};
    const data:CandleResponse={source:'MT5',synthetic:false,price_basis:'BID',latest_unconfirmed:true,
      candles:[closed,{...closed,time:300,high:99}]};
    const snapshot=closedCandles(data);
    data.candles.push({...closed,time:600});
    expect(snapshot).toEqual([closed]);
    expect(visibleAt([{...closed,available_at:3700}],3600,3600)).toEqual([]);
    expect(visibleAt([closed],3600,3600)).toEqual([closed]);
  });
  it('uses historical London and New York timezone offsets in spring and autumn',()=>{
    expect(sessionBounds(at('2026-03-20T12:00:00Z'),'Europe/London','08:00-16:00')?.[0]).toBe(at('2026-03-20T08:00:00Z'));
    expect(sessionBounds(at('2026-04-01T12:00:00Z'),'Europe/London','08:00-16:00')?.[0]).toBe(at('2026-04-01T07:00:00Z'));
    expect(sessionBounds(at('2026-10-10T12:00:00Z'),'Europe/London','08:00-16:00')?.[0]).toBe(at('2026-10-10T07:00:00Z'));
    expect(sessionBounds(at('2026-11-04T12:00:00Z'),'Europe/London','08:00-16:00')?.[0]).toBe(at('2026-11-04T08:00:00Z'));
    expect(sessionBounds(at('2026-03-01T12:00:00Z'),'America/New_York','08:00-16:00')?.[0]).toBe(at('2026-03-01T13:00:00Z'));
    expect(sessionBounds(at('2026-03-20T12:00:00Z'),'America/New_York','08:00-16:00')?.[0]).toBe(at('2026-03-20T12:00:00Z'));
    expect(sessionBounds(at('2026-11-04T12:00:00Z'),'America/New_York','08:00-16:00')?.[0]).toBe(at('2026-11-04T13:00:00Z'));
    expect(sessionBounds(at('2026-11-01T12:00:00Z'),'America/New_York','01:00-16:00')).toBeUndefined();
  });
  it('validates workspace version and cannot write non-workspace objects',()=>{
    expect(parsePreset({version:1,name:'SMT',preferences:{source:'MT5',timezone:'Asia/Kolkata'}}).name).toBe('SMT');
    expect(()=>parsePreset({version:1,name:'Invalid',preferences:{'tradeforge.planner.draft.v1':'unsafe'}})).toThrow();
    expect(()=>parsePreset({version:3,name:'Future',preferences:{source:'DEMO'}})).toThrow();
  });
  it('maps every hourly Forex coverage API status to its expected badge tone',()=>{
    expect(hourlyCoverageTone('Healthy')).toBe('green');
    expect(hourlyCoverageTone('Conflicting evidence')).toBe('red');
    expect(hourlyCoverageTone('Scheduled closure')).toBe('neutral');
    expect(hourlyCoverageTone('Partial')).toBe('amber');
    expect(hourlyCoverageTone('Missing open time')).toBe('amber');
    expect(hourlyCoverageTone('Unexpected status')).toBe('amber');
  });
});
