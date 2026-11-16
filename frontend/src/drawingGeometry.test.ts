import {describe,expect,it} from 'vitest';
import type {Drawing} from './Drawings';
import {handlesFor,sameAnchors,transformDrawing} from './drawingGeometry';

const drawing=(kind:Drawing['kind'],points:[number,string][]):Drawing=>({
  id:'fixture',kind,anchors:points.map(([time,price])=>({time,price})),
  visible:true,locked:false,color:'#55d9b5',label:'',created_at:1,updated_at:1,
});
const at=(time:number,price:number)=>({x:0,y:0,time,price});

describe('direct drawing geometry',()=>{
  it('maps rectangle corner axes independently',()=>{
    const source=drawing('rectangle',[[180,'11.00'],[360,'9.00']]);
    const corner=handlesFor('rectangle',[{x:10,y:20},{x:40,y:50}])[2];
    expect(corner).toEqual({x:10,y:50,anchor:{timeIndex:0,priceIndex:1}});
    expect(transformDrawing(source,corner.anchor,at(180,9),at(120,8),2,()=>true)).toEqual([
      {time:120,price:'11.00'},{time:360,price:'8.00'},
    ]);
    expect(source.anchors[1].price).toBe('9.00');
  });
  it('preserves offsets when moving and rejects unmatched timestamps',()=>{
    const source=drawing('trend',[[180,'10.10'],[360,'10.40']]);
    const moved=transformDrawing(source,undefined,at(240,10.2),at(300,10.3),2,time=>[240,420].includes(time));
    expect(moved).toEqual([{time:240,price:'10.20'},{time:420,price:'10.50'}]);
    expect(transformDrawing(source,undefined,at(240,10.2),at(300,10.3),2,time=>time===240)).toBeUndefined();
    expect(sameAnchors(source.anchors,[{time:180,price:'10.1'},{time:360,price:'10.40'}])).toBe(true);
  });
  it('rejects zero risk, crossed stops and targets or an invalid entry',()=>{
    const long=drawing('risk-reward',[[180,'10.0'],[240,'9.0'],[300,'12.0']]);
    const short=drawing('risk-reward',[[180,'10.0'],[240,'12.0'],[300,'9.0']]);
    const stop={timeIndex:1,priceIndex:1},target={timeIndex:2,priceIndex:2},entry={timeIndex:0,priceIndex:0};
    expect(transformDrawing(long,stop,at(240,9),at(240,10),2,()=>true)).toBeUndefined();
    expect(transformDrawing(short,target,at(300,9),at(300,11),2,()=>true)).toBeUndefined();
    expect(transformDrawing(short,entry,at(180,10),at(180,8),2,()=>true)).toBeUndefined();
    expect(transformDrawing(long,undefined,at(240,10),at(300,11),2,()=>true)?.map(p=>p.price)).toEqual(['11.00','10.00','13.00']);
  });
  it('moves a horizontal price without changing its timestamp and rejects negative prices',()=>{
    const source=drawing('horizontal',[[180,'10.0']]);
    const handle={timeIndex:0,priceIndex:0};
    expect(transformDrawing(source,handle,at(180,10),at(300,10.24),2,()=>false))
      .toEqual([{time:180,price:'10.24'}]);
    expect(transformDrawing(source,handle,at(180,10),at(300,-0.5),2,()=>true)).toBeUndefined();
  });
});
