import type {Page} from '@playwright/test';

// Instrument the real public chart API and browser resources in this browser context only.
export async function installProbe(page:Page) {
  await page.addInitScript(()=>{
    const counts={charts:0,subscriptions:0,observers:0,listeners:0,timers:0,frames:0};
    const live=new Map<Element,any>();
    const audit={bars:()=>Array.from(live.values(),api=>api.panes()[0].getSeries()[0].data()),
      point:(time:number)=>{
        const el=document.querySelector('.primary-chart .chart-input')!;
        const api=live.get(el), rect=el.getBoundingClientRect();
        return {x:rect.left+api.priceScale('left').width()+api.timeScale().timeToCoordinate(time),y:rect.top+rect.height*.65};
      },range:()=>Array.from(live.values())[0]?.timeScale().getVisibleLogicalRange(),counts,created:0,disposed:0,retain:false,retained:new Set<Function>(),linkedCursor:0,linkedRange:0,
      chart:(factory:Function)=>(el:Element,options:unknown)=>{
        const api=factory(el,options);counts.charts++;audit.created++;live.set(el,api);
        function subscription(target:any, suffix:string) {
          const sub='subscribe'+suffix,unsub='unsubscribe'+suffix;
          const add=target[sub].bind(target),remove=target[unsub].bind(target);
          const callbacks=new Set<Function>();
          target[sub]=(fn:Function)=>{add(fn);if(!callbacks.has(fn)){callbacks.add(fn);counts.subscriptions++;}};
          target[unsub]=(fn:Function)=>{
            if(audit.retain&&suffix==='Click'){audit.retained.add(fn);return;}
            remove(fn);if(callbacks.delete(fn))counts.subscriptions--;
          };
        }
        subscription(api,'Click');subscription(api,'CrosshairMove');
        const scale=api.timeScale();
        subscription(scale,'VisibleLogicalRangeChange');
        const cursor=api.setCrosshairPosition.bind(api);
        api.setCrosshairPosition=(...args:unknown[])=>{audit.linkedCursor++;return cursor(...args);};
        const move=scale.setVisibleRange.bind(scale);
        scale.setVisibleRange=(...args:unknown[])=>{audit.linkedRange++;return move(...args);};
        const dispose=api.remove.bind(api);
        api.remove=()=>{dispose();live.delete(el);counts.charts--;audit.disposed++;};
        return api;
      }};
    Object.assign(window,{workspaceProbe:audit});
    // Only direct application resource acquisitions. Ignore Playwright, React delegation
    // and library-private internals, except observers actually attached to a chart surface.
    const owned=(stack:string|undefined)=>/\/src\/(App|components|drawingPlacement|ChartLink|chartViews|indicators|ReplayControls)\.[tj]sx?/.test(stack?.split('\n')[2]??'');
    const originalAdd=EventTarget.prototype.addEventListener, originalRemove=EventTarget.prototype.removeEventListener;
    const registrations=new WeakMap<EventTarget, Map<string,Set<unknown>>>();
    const key=(type:string,opt?:boolean|EventListenerOptions)=>type+':'+Boolean(typeof opt==='boolean'?opt:opt?.capture);
    EventTarget.prototype.addEventListener=function(type,cb,opt){
      if(cb&&owned(new Error().stack)){
        let target=registrations.get(this);if(!target)registrations.set(this,target=new Map());
        const k=key(type,opt);let set=target.get(k);if(!set)target.set(k,set=new Set());
        if(!set.has(cb)){set.add(cb);counts.listeners++;}
      }
      originalAdd.call(this,type,cb,opt);
    };
    EventTarget.prototype.removeEventListener=function(type,cb,opt){
      if(registrations.get(this)?.get(key(type,opt))?.delete(cb))counts.listeners--;
      originalRemove.call(this,type,cb,opt);
    };
    const Original=window.ResizeObserver;
    window.ResizeObserver=class extends Original{
      private targets=new Set<Element>();
      observe(el:Element,opt?:ResizeObserverOptions){super.observe(el,opt);
        if(el.closest('.chart-input')&&!this.targets.has(el)){this.targets.add(el);counts.observers++;}}
      unobserve(el:Element){super.unobserve(el);if(this.targets.delete(el))counts.observers--;}
      disconnect(){super.disconnect();counts.observers-=this.targets.size;this.targets.clear();}
    };
    for(const [add,remove,field] of [['setTimeout','clearTimeout','timers'],['setInterval','clearInterval','timers'],
      ['requestAnimationFrame','cancelAnimationFrame','frames']] as const){
      const w=window as any, start=w[add].bind(w), stop=w[remove].bind(w), ids=new Set<number>();
      w[add]=(cb:Function,...args:unknown[])=>{
        if(!owned(new Error().stack))return start(cb,...args);
        const id=start((...values:unknown[])=>{
          if(add!=='setInterval'&&ids.delete(id))counts[field]--;
          return cb(...values);
        },...args);ids.add(id);counts[field]++;return id;
      };
      w[remove]=(id:number)=>{if(ids.delete(id))counts[field]--;return stop(id);};
    }
  });
}
