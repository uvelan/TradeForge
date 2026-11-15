import {useCallback,useEffect,useMemo,useState} from 'react';
import { Crosshair, Eraser,   ListFilter,  Minus,  Pause, Play, SkipBack, SkipForward,  Activity,Archive,ArrowDownToLine,ArrowRight,ArrowUpRight,BarChart3,BookOpen,BrainCircuit,CalendarClock,CheckCircle2,ChevronRight,Clock3,CloudOff,Database,Download,ExternalLink,FileCheck2,FileText,FlaskConical,FolderOpen,LayoutDashboard,Menu,MoreHorizontal,Plus,Radio,RefreshCw,Settings,ShieldCheck,ShieldAlert,Search,Target,TrendingUp,Upload, X, Zap} from 'lucide-react';
import {api} from './api';
import {type ComparisonPlacement,Badge,CandleChart,Card,Empty,Metric,Notice,PriceChange,SectionTitle,Select,Spinner,Success,TextArea,TextField} from './components';
import type {BacktestResult,BrokerSymbol,CandleRange,CandleResponse,ChartSource,Dataset,JournalEntry,Overview,Package,Plan,Review,SourceName,SymbolName,Timeframe,Trade} from './types';
import {SavedRange} from './SavedRange';
import {useBinanceCandles} from './useBinanceCandles';
import './style.css';
import './workspace.css';
import {TimezonePicker,useTimestampFormat,useTimezone} from './timezone';
import {CandleColorSettings} from './candleColors';
import {useWorkspacePreference} from './useWorkspacePreference';
import {ChartView,frameMinutes,useViewCandles} from './chartViews';
import {useChartIndicator} from './indicators';
import {smtFeed,useSmt} from './smt';

type Section='overview'|'terminal'|'plans'|'ai'|'lab'|'journal'|'data'|'settings';
const menu:{id:Section;name:string;icon:typeof Activity}[]=[
  {id:'overview',name:'Overview',icon:LayoutDashboard},
  {id:'terminal',name:'Market Terminal',icon:Activity},
  {id:'plans',name:'Trade Planner',icon:Target},
  {id:'ai',name:'AI Review Center',icon:BrainCircuit},
  {id:'lab',name:'Strategy Lab',icon:FlaskConical},
  {id:'journal',name:'Learning Journal',icon:BookOpen},
  {id:'data',name:'Data Library',icon:Database},
  {id:'settings',name:'Settings',icon:Settings}
];
const initialPlan:Plan={symbol:'EURUSD',direction:'BUY',strategy:'CRT / Market Structure',session:'LONDON',timeframe:'M5',context:'',observation:'',evidence:'',hypothesis:'',setup:'',trigger:'',invalidation:'',management:'No automatic execution. Manual exit at planned levels.',entry:'',stop:'',target:'',equity_usd:'10000',risk_percent:'0.5',pip_value_per_lot_usd:'10'};
const snippets={context:'H1 structure and session trend; verify higher timeframe bias.',observation:'M5 price swept a prior session extreme and re-entered the range.',evidence:'Document exact candle times and levels; no historical edge established.',hypothesis:'If reclaim holds, next impulse may reach opposite range liquidity.',setup:'Candidate range and sweep: unconfirmed until close.',trigger:'Wait for M5 close back inside the reference range.',invalidation:'Structure breaks beyond the sweep with a completed candle.'};
function App(){
  const {zone}=useTimezone();
  const [section,setSection]=useWorkspacePreference<Section>('page','overview',menu.map(item=>item.id));const [sidebar,setSidebar]=useState(false);const [reload,setReload]=useState(0);
  const [overview,setOverview]=useState<Overview|null>(null);const [backendError,setBackendError]=useState('');
  const refresh=useCallback(()=>setReload(v=>v+1),[]);
  useEffect(()=>{let mounted=true;api.overview().then(x=>{if(mounted){setOverview(x);setBackendError('')}}).catch(e=>{if(mounted)setBackendError(e.message)});return()=>{mounted=false}},[reload]);
  const go=(s:Section)=>{setSection(s);setSidebar(false)};
  return <div className="app-shell">
    <aside className={'sidebar '+(sidebar?'side-open':'')}>
      <div className="brand"><div className="brand-mark"><TrendingUp size={23} strokeWidth={2.7}/></div><div><strong>Trade<span>Forge</span></strong><small>INTELLIGENCE TERMINAL</small></div></div>
      <div className="workspace-name"><div className="workspace-avatar">TF</div><div className="workspace-text"><strong>Personal Workspace</strong><span>Local desktop environment</span></div><ChevronRight size={15} className="faint"/></div>
      <div className="nav-category">WORKSPACE</div><nav>{menu.slice(0,5).map(m=><button className={'nav-item '+(section===m.id?'selected':'')} key={m.id} onClick={()=>go(m.id)}><m.icon size={18} strokeWidth={1.8}/><span>{m.name}</span>{m.id==='ai'&&<span className="tiny-pulse"/>}</button>)}</nav>
      <div className="nav-category nav-category-second">LIBRARY & CONFIG</div><nav>{menu.slice(5).map(m=><button className={'nav-item '+(section===m.id?'selected':'')} key={m.id} onClick={()=>go(m.id)}><m.icon size={18} strokeWidth={1.8}/><span>{m.name}</span></button>)}</nav>
      <div className="sidebar-bottom">
        <div className="risk-safety"><ShieldCheck size={18}/><div><strong>Execution Locked</strong><p>No automated orders enabled</p></div></div>
        <div className="profile"><div className="profile-avatar">U</div><div><strong>Local Trader</strong><span>Single-user edition</span></div><MoreHorizontal size={18}/></div>
      </div>
    </aside>
    {sidebar&&<button className="mobile-shade" aria-label="Close navigation" onClick={()=>setSidebar(false)}/>}
    <main className="main">
      <header className="topbar">
        <div className="topbar-start"><button className="icon-btn mobile-menu" onClick={()=>setSidebar(true)} aria-label="Open navigation"><Menu size={21}/></button><div className="breadcrumb">Workspace <ChevronRight size={15}/> <strong>{menu.find(m=>m.id===section)?.name}</strong></div></div>
        <div className="topbar-end"><div className="topbar-mode"><span className="dot green"/> LOCAL MODE</div><div className="topbar-sep"/><span className="topbar-date"><CalendarClock size={16}/> {new Date().toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric',timeZone:zone})}</span><button className="icon-btn" aria-label="Refresh data" onClick={refresh}><RefreshCw size={17}/></button><div className="top-avatar">U</div></div>
      </header>
      {backendError&&<div className="error-banner"><CloudOff size={18}/> API offline: {backendError}. Start the API using start-api.ps1.</div>}
      <div className="page-content" key={section}>
      {section==='overview'&&<Dashboard overview={overview} go={go}/>}
      {section==='terminal'&&<Terminal expanded/>}
      {section==='plans'&&<Planner refresh={refresh}/>}
      {section==='ai'&&<AIPage refresh={refresh}/>}
      {section==='lab'&&<StrategyLab/>}
      {section==='journal'&&<Journal refresh={refresh}/>}
      {section==='data'&&<DataLibrary refresh={refresh}/>}
      {section==='settings'&&<SettingsPage/>}
      </div>
      <footer className="footer"><span>TRADEFORGE • RESEARCH ENVIRONMENT • SINGLE USER</span><span>Charts by <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView</a> · UTC storage · {zone}</span></footer>
    </main>
  </div>
}

function PageHeading({eyebrow,title,subtitle,action}:{eyebrow:string;title:string;subtitle:string;action?:React.ReactNode}){return <div className="page-heading"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{subtitle}</p></div>{action}</div>}

const watchSymbols:SymbolName[]=['EURUSD','GBPUSD','USDJPY','XAUUSD'];
const chartSources:ChartSource[]=['MT5','MT5_SAVED','DUKASCOPY_SAVED','BINANCE_SPOT','DEMO'];
const chartTimeframes:Timeframe[]=['M1','M3','M5','M15','M30','H1','H4','D1'];
type ChartLevel=import('./components').ChartLevel;
function Terminal({expanded=false}:{expanded?:boolean}){
  const {zone}=useTimezone();
  const [forexSymbol,setSymbol]=useWorkspacePreference<SymbolName>('forex-symbol','EURUSD',watchSymbols);
  const [timeframe,setTimeframe]=useWorkspacePreference<Timeframe>('timeframe','M5',chartTimeframes);
  const [source,setSource]=useWorkspacePreference<ChartSource>('source','MT5',chartSources);
  const [selectedRange,setRange]=useState<CandleRange>();
  const savedSource=source==='MT5_SAVED'||source==='DUKASCOPY_SAVED';
  const range=savedSource?selectedRange:undefined;
  const isBinance=source==='BINANCE_SPOT';
  const [brokerSymbol,setBrokerSymbol]=useWorkspacePreference<string>('broker-symbol','EURUSD');
  const [instruments,setInstruments]=useState<BrokerSymbol[]>([]);
  const [symbolQuery,setSymbolQuery]=useState('');
  const [symbolError,setSymbolError]=useState('');
  const [pollError,setPollError]=useState('');
  const symbol=isBinance?'BTCUSDT':source==='MT5'?brokerSymbol:forexSymbol;
  const digits=instruments.find(item=>item.name===symbol)?.digits ?? (isBinance?2:symbol==='EURUSD'||symbol==='GBPUSD'?5:3);
  const [data,setData]=useState<CandleResponse|null>(null);
  const [error,setError]=useState('');const [busy,setBusy]=useState(true);
  const [refreshCount,setRefreshCount]=useState(0);
  const [viewCount,setViewCount]=useWorkspacePreference('chart-views','1',['1','2','3','4']);
  const [comparisonSymbol,setComparisonSymbol]=useWorkspacePreference<string>('chart-comparison','none');
  const [showSmt,setShowSmt]=useWorkspacePreference('show-smt','on',['on','off']);
  const [smtWidth,setSmtWidth]=useWorkspacePreference<string>('smt-width','3');
  const [smtAlignment,setSmtAlignment]=useWorkspacePreference<string>('smt-alignment','2');
  const [smtTolerance,setSmtTolerance]=useWorkspacePreference<string>('smt-tolerance','0.5');
  const [smtConfirmation,setSmtConfirmation]=useWorkspacePreference<string>('smt-confirmation','1');
  const [placement,setPlacement]=useWorkspacePreference<ComparisonPlacement>('comparison-placement','Overlay',['Overlay','New pane']);
  const [drawMode,setDrawMode]=useState(false);
  const [levels,setLevels]=useState<ChartLevel[]>([]);
  const [replayIndex,setReplayIndex]=useState<number|null>(null);
  const [playing,setPlaying]=useState(false);
  const [quotes,setQuotes]=useState<Record<string,number|null>>({});
  const refreshCandles=useCallback(()=>setRefreshCount(n=>n+1),[]);
  const live=useBinanceCandles(isBinance,timeframe,refreshCandles);
  useEffect(()=>{
    if(source!=='MT5')return;
    let active=true;
    api.brokerSymbols().then(items=>{
      if(!active)return;
      setSymbolError('');
      setInstruments(items);
      setBrokerSymbol(current=>items.some(item=>item.name===current)?current:
        items.find(item=>item.name.toUpperCase().includes('EURUSD'))?.name ?? items[0]?.name ?? current);
    }).catch(error=>{if(active)setSymbolError(error.message)});
    return()=>{active=false};
  },[source,refreshCount]);
  useEffect(()=>{
    let active=true;
    setBusy(true);setError('');setPollError('');setData(null);setReplayIndex(null);setPlaying(false);
    api.candles(symbol,timeframe,source,expanded?600:200,source==='MT5',range)
      .then(main=>{if(active)setData(main)})
      .catch(e=>{if(active){setError(e.message);setData(null)}})
      .finally(()=>{if(active)setBusy(false)});
    return()=>{active=false};
  },[symbol,timeframe,source,expanded,refreshCount,range]);
  useEffect(()=>{
    if(source!=='MT5'||busy||error||replayIndex!==null)return;
    let active=true;
    let pending=false;
    const timer=window.setInterval(async()=>{
      if(pending||document.hidden)return;
      pending=true;
      try{
        const update=await api.candles(symbol,timeframe,'MT5',10,true);
        if(active){
          setPollError('');
          setData(current=>{
            if(!current||!update.candles.length)return current;
            if(current.feed_id!==update.feed_id||current.price_basis!==update.price_basis)return update;
            const first=update.candles[0].time;
            const last=current.candles.at(-1)?.time ?? 0;
            if(update.candles.at(-1)!.time<last)return current;
            return {...update,candles:[...current.candles.filter(row=>row.time<first),...update.candles].slice(-(expanded?600:200))};
          });
        }
      }catch{
        if(active)setPollError('MT5 update unavailable · showing last received bars');
      }finally{pending=false}
    },5000);
    return()=>{active=false;window.clearInterval(timer)};
  },[source,symbol,timeframe,busy,error,replayIndex,expanded]);
  useEffect(()=>{
    if(!expanded)return;
    setQuotes({});
    if(source!=='MT5' && source!=='DEMO')return;
    let active=true;
    const update=()=>Promise.all(watchSymbols.map(async s=>{
      try{const q=await api.quote(s,source==='MT5'?'MT5':'DEMO');return [s,q.bid] as const}
      catch{return [s,null] as const}
    })).then(entries=>{if(active)setQuotes(Object.fromEntries(entries))});
    void update();
    const timer=window.setInterval(()=>{if(source==='MT5')void update()},10000);
    return()=>{active=false;window.clearInterval(timer)};
  },[expanded,source]);
  useEffect(()=>{
    if(!expanded)return;
    try{
      const raw=window.localStorage.getItem(`tf-levels:${source}:${symbol}:${timeframe}`);
      const arr=raw?JSON.parse(raw):[];
      setLevels(Array.isArray(arr)?arr.filter((v:ChartLevel)=>typeof v.price==='number'&&Number.isFinite(v.price)).slice(0,50):[]);
    }catch{setLevels([])}
  },[expanded,source,symbol,timeframe]);
  const addLevel=(price:number)=>{
    if(!Number.isFinite(price))return;
    const decimal=digits;
    const next=[...levels,{id:`${Date.now()}-${levels.length}`,price:Number(price.toFixed(decimal))}].slice(-50);
    setLevels(next);
    try{window.localStorage.setItem(`tf-levels:${source}:${symbol}:${timeframe}`,JSON.stringify(next))}catch{/* Browser storage unavailable */}
    setDrawMode(false);
  };
  const clearLevels=()=>{
    setLevels([]);
    try{window.localStorage.removeItem(`tf-levels:${source}:${symbol}:${timeframe}`)}catch{/* Browser storage unavailable */}
  };
  const historical=data?.source===source?(data.latest_unconfirmed&&replayIndex!==null?data.candles.slice(0,-1):data.candles):[];
  const liveRows=live?.candles ?? [];
  const candles=isBinance&&liveRows.length
    ? [...new Map([...historical,...liveRows].map(row=>[row.time,row])).values()].sort((a,b)=>a.time-b.time).slice(-600)
    : historical;
  const replayActive=replayIndex!==null;
  const visibleCandles=replayIndex===null?candles:candles.slice(0,replayIndex+1);
  const indicatorCandles=replayIndex===null&&data?.latest_unconfirmed?visibleCandles.slice(0,-1):visibleCandles;
  const indicator=useChartIndicator(expanded&&!isBinance&&!busy&&!error,indicatorCandles,source,timeframe);
  const last=visibleCandles.at(-1);const first=visibleCandles[0];
  const change=last&&first?(last.close-first.open)/first.open*100:0;
  const canReplay=expanded&&!isBinance&&candles.length>31;
  const beginReplay=()=>{if(!canReplay)return;setReplayIndex(Math.max(20,Math.floor(candles.length*.6)));setPlaying(false)};
  const stepReplay=useCallback((delta:number)=>setReplayIndex(i=>i===null?null:Math.max(0,Math.min(candles.length-1,i+delta))),[candles.length]);
  useEffect(()=>{
    if(!playing||replayIndex===null||replayIndex>=candles.length-1){if(playing&&replayIndex!==null&&replayIndex>=candles.length-1)setPlaying(false);return}
    const timer=window.setInterval(()=>setReplayIndex(i=>i===null?null:Math.min(candles.length-1,i+1)),750);
    return()=>window.clearInterval(timer);
  },[playing,replayIndex,candles.length]);
  useEffect(()=>{
    if(!expanded)return;
    const onKey=(e:KeyboardEvent)=>{
      const target=e.target as HTMLElement|null;
      if(target?.closest('input,textarea,select,[contenteditable="true"]'))return;
      if(e.altKey&&e.key.toLowerCase()==='h'){e.preventDefault();setDrawMode(v=>!v)}
      if(replayActive&&e.code==='Space'){e.preventDefault();setPlaying(v=>!v)}
      if(replayActive&&e.key==='ArrowRight'&&e.shiftKey){e.preventDefault();stepReplay(1)}
      if(e.key==='Escape')setDrawMode(false);
    };
    window.addEventListener('keydown',onKey);return()=>window.removeEventListener('keydown',onKey);
  },[expanded,replayActive,stepReplay]);
  const boundary=last?.time??0;
  const cutoff=replayActive?boundary+frameMinutes[timeframe]*60:undefined;
  const options=[...new Set([symbol,...(isBinance?[]:source==='MT5'?instruments.map(item=>item.name):watchSymbols)])];
  const compare=expanded&&comparisonSymbol!==symbol&&options.includes(comparisonSymbol)?comparisonSymbol:'';
  const comparison=useViewCandles(compare,timeframe,source,refreshCount,cutoff,true,range);
  const rules={period:Number(smtWidth),alignment_bars:Number(smtAlignment),
    tolerance_bps:Number(smtTolerance),confirmation_bars:Number(smtConfirmation)};
  const ruleInputs=[
    ['Swing width',smtWidth,setSmtWidth,2,20,1],
    ['UTC match (bars)',smtAlignment,setSmtAlignment,0,10,1],
    ['Tolerance (bps)',smtTolerance,setSmtTolerance,0,100,0.1],
    ['Confirmation bars',smtConfirmation,setSmtConfirmation,0,20,1],
  ] as const;
  const validRules=ruleInputs.every(([,value,,min,max,step])=>value!==''&&Number(value)>=min&&
    Number(value)<=max&&(step!==1||Number.isInteger(Number(value))));
  const firstFeed=smtFeed(symbol,data?.source===source?data:undefined);
  const secondFeed=smtFeed(compare,comparison.feed);
  const missingSmtFeed=source!=='DEMO'&&Boolean(compare)&&Boolean(data&&comparison.feed)&&(!firstFeed||!secondFeed);
  const smt=useSmt(indicatorCandles,comparison.candles,timeframe,showSmt==='on'&&Boolean(compare)&&!range&&!busy&&!error&&validRules&&
    (source==='DEMO'||Boolean(firstFeed&&secondFeed)),rules,firstFeed,secondFeed,replayActive,refreshCount);
  const precisionFor=(name:string)=>instruments.find(item=>item.name===name)?.digits ?? (name==='BTCUSDT'?2:name==='USDJPY'||name==='XAUUSD'?3:5);

  const pairText=symbol==='XAUUSD'?'Gold / US Dollar':`${symbol.slice(0,3)} / ${symbol.slice(3)}`;
  const label=isBinance?`BINANCE SPOT · ${live?.status ?? 'Connecting'}`:source==='DEMO'?'SYNTHETIC · NOT LIVE':source==='MT5'?'MT5 BROKER FEED':source.replaceAll('_',' ')+' · ARCHIVED';
  const matchedSymbols=useMemo(()=>{
    const query=symbolQuery.trim().toLowerCase();
    return instruments.filter(item=>{
      const alias=['USTEC','USTECH100M'].includes(item.name)?' Nasdaq 100':'';
      return `${item.name} ${item.description}${alias}`.toLowerCase().includes(query);
    });
  },[instruments,symbolQuery]);
  const preferredPairs=['EURUSD','GBPUSD','USDJPY','USDCHF','AUDUSD','USDCAD','NZDUSD','USTEC','USTECH100M','XAUUSD','XAGUSD'];
  const rankedSymbols=matchedSymbols.slice().sort((a,b)=>{
    const rank=(name:string)=>{
      const index=preferredPairs.findIndex(pair=>name.toUpperCase().includes(pair));
      return index<0?preferredPairs.length:index;
    };
    if(symbolQuery.trim()) {
      const prefix=symbolQuery.trim().toUpperCase();
      return Number(!a.name.toUpperCase().startsWith(prefix))-Number(!b.name.toUpperCase().startsWith(prefix));
    }
    return rank(a.name)-rank(b.name);
  });
  const brokerOptions=[brokerSymbol,...rankedSymbols.filter(item=>item.name!==brokerSymbol).slice(0,100).map(item=>item.name)];
  const brokerPicker=source==='MT5'&&<div className="broker-picker">
    <label className="broker-search">
      <Search size={15} aria-hidden="true"/>
      <input aria-label="Search broker symbols" placeholder="Search forex, indices, metals" value={symbolQuery} onChange={event=>setSymbolQuery(event.target.value)}/>
    </label>
    <Select label="Broker instrument" value={brokerSymbol} options={brokerOptions} onChange={setBrokerSymbol}/>
    {(symbolQuery.trim() || symbolError)&&<small className="broker-search-status" role="status">
      {symbolError||(matchedSymbols.length===0?'No matching instruments':`${matchedSymbols.length.toLocaleString()} matches${matchedSymbols.length>100?' · showing first 100':''}`)}
    </small>}
  </div>;
  const latestNotice=!replayActive&&data?.latest_unconfirmed?'Latest MT5 bar · close unconfirmed':'';
  if(!expanded)return <Card className="market-card">
    <div className="market-header"><div className="market-title"><div className="pair-icon"><Activity size={19}/></div><div><div className="market-pair-row"><h3>{pairText}</h3><Badge tone={source==='DEMO'?'amber':'blue'}>{source==='DEMO'?'SIMULATED':source}</Badge></div><p>Market context · research environment</p></div></div><div className="price-head">{last?<><strong>{last.close.toFixed(digits)}</strong><PriceChange value={change}/></>:<strong>—</strong>}</div></div>
    <div className="chart-toolbar"><div className="timeframe-buttons">{(['M1','M3','M5','M15','H1','H4','D1'] as Timeframe[]).map(t=><button key={t} className={timeframe===t?'on':''} onClick={()=>setTimeframe(t)}>{t}</button>)}</div><div className="chart-actions">{!isBinance&&source!=='MT5'&&<Select value={forexSymbol} options={watchSymbols} onChange={s=>setSymbol(s as SymbolName)}/>}<Select label="Market data source" value={source} options={chartSources} onChange={s=>setSource(s as ChartSource)}/></div>{brokerPicker}</div>
    <div className="chart-frame">{busy?<div className="chart-loading"><Spinner/> Loading candles</div>:error?<div className="chart-loading error-text"><CloudOff/> {error}</div>:candles.length?<CandleChart candles={candles} height={300} symbol={symbol} precision={digits}/>:<Empty title="No candles" body="Connect MT5 or import a validated dataset."/>}</div>
    <div className="chart-footer"><span>{label} {latestNotice} {pollError}</span><span>{candles.length} candles · {zone}</span></div>
  </Card>;
  return <div className="terminal-suite">
    <div className="terminal-suite-header"><div><span className="eyebrow">MARKET ANALYSIS / CHART WORKSPACE</span><h1>Advanced chart terminal</h1><p>Chart-first research · manual trading · <strong>no automated execution</strong></p></div><div className="terminal-hint"><ShieldCheck size={17}/> Local & read-only</div></div>
    <div className="terminal-layout">
      <section className="terminal-core">
        <div className="terminal-identity"><div className="terminal-pair"><div className="pair-badge">{symbol.slice(0,3)}</div><div><strong>{pairText}</strong><span>{label}</span><small>{latestNotice} {pollError}</small></div></div><div className="terminal-head-price">{last?<><strong>{last.close.toFixed(digits)}</strong><PriceChange value={change}/></>:<span>—</span>}</div></div>
        <div className="terminal-controls"><div className="timeframe-buttons">{(['M1','M3','M5','M15','M30','H1','H4','D1'] as Timeframe[]).map(t=><button key={t} className={timeframe===t?'on':''} onClick={()=>setTimeframe(t)}>{t}</button>)}</div><div className="terminal-control-right"><Select label="Market data source" value={source} options={chartSources} onChange={s=>setSource(s as ChartSource)}/><Select label="Chart layout" value={viewCount} options={['1','2','3','4']} onChange={value=>setViewCount(value as typeof viewCount)}/><button className="small-tool" onClick={()=>setRefreshCount(n=>n+1)} title="Refresh candles"><RefreshCw size={16}/></button></div>{brokerPicker}</div>
        {savedSource&&<SavedRange range={range} onChange={setRange}/>}
        {data?.coverage&&<div className="indicator-controls" role="status">
          {data.coverage.gaps.length} UTC coverage gaps · {data.coverage.duplicate_bars} duplicate bars · {data.coverage.truncated?'Showing latest 600 candles':'Requested range loaded'}
        </div>}
        {indicator.controls}<div className="indicator-controls comparison-controls"><label>SMT comparison
          <Select label="SMT comparison" value={compare||'none'} options={['none',...options.filter(item=>item!==symbol)]} onChange={setComparisonSymbol}/>
          </label>{compare&&<label><input className="smt-toggle" type="checkbox" checked={showSmt==='on'}
            onChange={event=>setShowSmt(event.target.checked?'on':'off')}/> Mark SMT</label>}
          {compare&&showSmt==='on'&&<details><summary>SMT rules</summary><div className="smt-rules">
            {ruleInputs.map(([label,value,setValue,min,max,step])=><label key={label}>{label}
              <input type="number" value={value} min={min} max={max} step={step} onChange={event=>setValue(event.target.value)}/>
            </label>)}
            {!validRules&&<small role="status">Enter values within the displayed limits.</small>}
          </div></details>}
          {compare&&showSmt==='on'&&<button type="button" className="small-tool"
            disabled={!validRules||busy||replayActive} onClick={refreshCandles}
            title={replayActive?'Exit replay to refresh market data':undefined}>
            <RefreshCw size={15}/> Refresh SMT data
          </button>}
          <small role="status">{smt.error || comparison.error || (compare&&range&&showSmt==='on'?'SMT verification requires a single saved partition per instrument':undefined) || (smt.authority==='CLIENT_SUPPLIED_UNVERIFIED'?'SMT preview · unverified candle input':undefined) || (missingSmtFeed ? 'SMT requires an identified feed and price basis for both instruments' : compare ? busy ? 'Refreshing chart data…' : smt.pending ? 'Recalculating SMT…' : comparison.candles?.length ? `${compare} · closed candles` : 'Waiting for comparison candles' : 'Compare instruments on the same chart')}</small>
        </div><div className="terminal-chart-zone"><div className="chart-tool-rail"><button className={'rail-tool '+(!drawMode?'active':'')} title="Crosshair (Esc)" onClick={()=>setDrawMode(false)}><Crosshair size={18}/></button><button className={'rail-tool '+(drawMode?'active':'')} title="Place horizontal level (Alt+H)" onClick={()=>setDrawMode(v=>!v)}><Minus size={19}/></button><div className="rail-separator"/><button className="rail-tool" title="Remove all saved levels" disabled={levels.length===0} onClick={clearLevels}><Eraser size={18}/></button><span className="rail-count">{levels.length}</span></div>
        <div className={'terminal-chart-columns '+(viewCount!=='1'?'multi-chart':'')}>
          <div className="primary-chart"><div className="pane-label"><span className="dot green"/>{symbol} · {timeframe} <span className="muted">{replayActive?'REPLAY':'PRICE'}</span></div>{busy?<div className="chart-loading"><Spinner/> Loading candles</div>:error?<div className="chart-loading error-text"><CloudOff/> {error}</div>:visibleCandles.length?<CandleChart key={`${symbol}-${timeframe}-${source}`} candles={visibleCandles} height={viewCount!=='1'?360:510} timeframe={timeframe} comparison={compare&&comparison.candles?.length?{symbol:compare,candles:comparison.candles,precision:precisionFor(compare),placement,onPlacementChange:setPlacement,smt:smt?.events}:undefined} symbol={symbol} precision={digits} indicator={indicator.data || undefined} levels={levels} drawMode={drawMode} onAddLevel={addLevel} showSnapshot/>:<Empty title="Market data unavailable" body="Connect MetaTrader 5 or import saved MT5 / Dukascopy candles."/>}</div>
          {Array.from({length:Number(viewCount)-1},(_,index)=><ChartView key={index+2} number={index+2}
            mainSymbol={symbol} source={source} options={options} refresh={refreshCount} cutoff={cutoff} precision={precisionFor} range={range}/>)}
        </div></div>
        <div className="terminal-replay"><div className="terminal-replay-left"><button className={'small-tool '+(replayActive?'active':'')} onClick={()=>replayActive?(setReplayIndex(null),setPlaying(false)):beginReplay()} disabled={!canReplay}><Clock3 size={17}/>{replayActive?'Exit replay':'Bar replay'}</button>{replayActive&&<><button className="small-tool icon-square" onClick={()=>setReplayIndex(Math.max(20,Math.floor(candles.length*.6)))} title="Restart replay"><SkipBack size={16}/></button><button className="small-tool icon-square" onClick={()=>stepReplay(1)} disabled={replayIndex===candles.length-1} title="Next candle (Shift+Right)"><SkipForward size={16}/></button><button className="small-tool active" onClick={()=>setPlaying(p=>!p)}>{playing?<Pause size={15}/>:<Play size={15}/>} {playing?'Pause':'Play'}</button><span className="replay-count">{(replayIndex??0)+1} / {candles.length}</span></>}</div><div className="terminal-footer-info">{replayActive?'Future candles hidden · replay':zone} <span className="footer-divider">|</span> {visibleCandles.length} bars · {data?.price_basis||'unverified'}</div></div>
      </section>
      <aside className="market-watchlist"><div className="watch-head"><div><span className="eyebrow">INSTRUMENTS</span><h3>Watchlist</h3></div><ListFilter size={17}/></div><div className="watch-columns"><span>PAIR</span><span>{isBinance?'LAST':'BID'}</span></div>{isBinance&&<div className='watch-row current'><strong>BTCUSDT</strong><span>{last?.close.toFixed(2)??'—'}</span></div>}{(isBinance?[]:watchSymbols).map(w=><button key={w} className={'watch-row '+((source==='MT5'?symbol===w||symbol.toUpperCase().includes(w):symbol===w)?'current':'')} onClick={()=>source==='MT5'?setBrokerSymbol(instruments.find(item=>item.name.toUpperCase().includes(w))?.name ?? w):setSymbol(w)}><span className="watch-symbol"><strong>{w}</strong><small>{w==='XAUUSD'?'Spot gold':w.slice(0,3)+' / '+w.slice(3)}</small></span><span className="watch-price">{quotes[w]===undefined||quotes[w]===null?'—':quotes[w]?.toFixed(w==='EURUSD'||w==='GBPUSD'?5:3)}</span></button>)}
        <div className="watch-detail"><span className="eyebrow">ACTIVE WORKSPACE</span><div><span>Feed</span><strong>{source}</strong></div><div><span>Instrument</span><strong>{symbol}</strong></div><div><span>Session clock</span><strong>{zone}</strong></div><p>{source==='DEMO'?'All displayed demo quotes are synthetic and are not suitable for market decisions.':'Broker/data availability is not guaranteed. Confirm quote freshness before use.'}</p></div>
        <div className="watch-shortcuts"><strong>Keyboard</strong><span>Alt + H <em>Horizontal level</em></span><span>Space <em>Play / pause replay</em></span><span>Shift + → <em>Next bar</em></span></div>
      </aside>
    </div>
  </div>;
}

function Dashboard({overview,go}:{overview:Overview|null;go:(s:Section)=>void}){
  const formatDate=useTimestampFormat();
  const a=overview?.analytics;return <>
    <PageHeading eyebrow="WORKSPACE" title="Overview" subtitle="Market data, trade plans, and research activity." action={<button className="btn btn-primary" onClick={()=>go('plans')}><Plus size={17}/> Create Trade Plan</button>}/>
    <div className="metrics-grid"><Metric title="Total trade plans" value={String(a?.trades??'—')} change="Immutable version history" icon={<FileText size={18}/>}/><Metric title="Active plans" value={String(a?.active_plans??'—')} change="Awaiting decision or review" icon={<Target size={18}/>}/><Metric title="Journal outcomes" value={String(a?.closed??'—')} change="Manually recorded outcomes" icon={<BarChart3 size={18}/>}/><Metric title="Historical expectancy" value={a?.expectancy_r==null?'—':`${a.expectancy_r>0?'+':''}${a.expectancy_r} R`} change={a?.closed?`${a.closed} recorded outcomes`:'Requires actual closed trades'} icon={<TrendingUp size={18}/>}/></div>
    <div className="dashboard-main"><div className="main-chart-column"><SectionTitle eyebrow="PRICE ACTION" title="Market overview" action={<button className="text-button" onClick={()=>go('terminal')}>Full terminal <ArrowUpRight size={15}/></button>}/><Terminal/></div><div className="right-column"><SectionTitle eyebrow="ENVIRONMENT" title="System readiness"/><Card className="readiness-card"><div className="readiness-item"><span className="status-icon"><Radio size={17}/></span><div><strong>MT5 terminal</strong><small>{overview?.mt5.connected?'Connected to broker':'Connection required'}</small></div><Badge tone={overview?.mt5.connected?'green':'amber'}>{overview?.mt5.connected?'ONLINE':'OFFLINE'}</Badge></div><div className="readiness-item"><span className="status-icon"><Database size={17}/></span><div><strong>Verified datasets</strong><small>Imported market data</small></div><strong>{overview?.datasets.length??0}</strong></div><div className="readiness-item"><span className="status-icon"><ShieldCheck size={17}/></span><div><strong>Order execution</strong><small>Read-only environment</small></div><Badge tone="green">LOCKED</Badge></div><div className="readiness-item"><span className="status-icon"><BrainCircuit size={17}/></span><div><strong>AI integration</strong><small>Offline export / import</small></div><Badge tone="blue">MANUAL</Badge></div></Card>
      <Card className="quick-card"><h3>Trade planning</h3><p>Document a setup and calculate its risk.</p><button className="btn btn-secondary full" onClick={()=>go('plans')}>Launch Trade Planner <ArrowRight size={16}/></button></Card></div></div>
    <div className="bottom-dashboard"><Card><SectionTitle eyebrow="WORKFLOW" title="Recent trade plans" action={<button className="text-button" onClick={()=>go('plans')}>View all <ArrowRight size={15}/></button>}/>{overview?.recent_trades.length?<div className="table-scroll"><table><thead><tr><th>ID</th><th>PAIR</th><th>BIAS</th><th>STATUS</th><th>CREATED</th></tr></thead><tbody>{overview.recent_trades.map(t=><tr key={t.id}><td className="mono">{t.id}</td><td><strong>{t.symbol}</strong></td><td><Badge tone={t.direction==='BUY'?'green':'red'}>{t.direction}</Badge></td><td>{t.status}</td><td>{formatDate(t.created_at)}</td></tr>)}</tbody></table></div>:<Empty icon={<FileText/>} title="No plans recorded yet" body="Create your first trading hypothesis to populate this timeline."/>}</Card>
    <Card><SectionTitle eyebrow="DISCIPLINE" title="Learning checkpoint"/><div className="checkpoint"><div className="check-progress"><div className="check-progress-inner">{overview?.journal_count??0}</div></div><div><strong>Master journal entries</strong><p>Each question, review, and corrected mistake becomes part of your cumulative history.</p></div></div><button className="btn btn-outline full" onClick={()=>go('journal')}>Open Learning Journal <ArrowRight size={16}/></button></Card></div>
    <div className="demo-strip"><ShieldAlert size={17}/><span>Research-only workspace. Demo candles are synthetic; no profitability or live execution is implied.</span></div>
  </>
}

function Planner({refresh}:{refresh:()=>void}){
  const formatDate=useTimestampFormat();
 const [list,setList]=useState<Trade[]>([]);const [loading,setLoading]=useState(true);const [formOpen,setFormOpen]=useState(false);const [plan,setPlan]=useState<Plan>({...initialPlan});const [risk,setRisk]=useState<import('./types').Risk|null>(null);const [busy,setBusy]=useState(false);const [message,setMessage]=useState('');const [error,setError]=useState('');const [closeId,setCloseId]=useState('');const [outcome,setOutcome]=useState({pnl:'0',r:'0',notes:''});
 const load=useCallback(()=>api.trades().then(setList).catch(e=>setError(e.message)).finally(()=>setLoading(false)),[]);useEffect(()=>{void load()},[load]);
 const modify=<K extends keyof Plan>(key:K,value:Plan[K])=>{setPlan(p=>({...p,[key]:value}));setRisk(null)};
 async function calculate(){setBusy(true);setError('');try{setRisk(await api.risk(plan))}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function save(){setBusy(true);setError('');try{const result=await api.createPlan(plan);setMessage('Created '+result.id);setFormOpen(false);setRisk(null);setPlan({...initialPlan});await load();refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function finalize(){if(!closeId)return;setBusy(true);setError('');try{await api.closeTrade(closeId,outcome.pnl,outcome.r,outcome.notes);setCloseId('');await load();refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <><PageHeading eyebrow="TRADING / PLANNING" title="Trade planner" subtitle="A trade should exist as a written hypothesis before it exists as a position." action={<button className="btn btn-primary" onClick={()=>{setFormOpen(true);setMessage('');setError('')}}><Plus size={17}/> New Trade Plan</button>}/>
  <div className="pill-guidance"><ShieldCheck size={17}/><div><strong>Evidence-first workflow</strong><span>Context → Observation → Evidence → Hypothesis → Setup → Trigger → Invalidation → Risk → Execution → Review</span></div></div>
  {message&&<Success>{message}</Success>}{error&&<Notice tone="red">{error}</Notice>}
  <Card><SectionTitle eyebrow="PLANNED DECISIONS" title="Your trade log" action={<Badge tone="neutral">{list.length} records</Badge>}/>{loading?<div className="loading-zone"><Spinner/></div>:list.length?<div className="table-scroll"><table><thead><tr><th>DECISION</th><th>SYMBOL</th><th>STRATEGY</th><th>RISK</th><th>STATUS</th><th>CREATED</th><th>ACTION</th></tr></thead><tbody>{list.map(t=><tr key={t.id}><td className="mono">{t.id}</td><td><strong>{t.symbol}</strong> <span className={t.direction==='BUY'?'positive':'negative'}>{t.direction}</span></td><td>{t.plan.strategy}</td><td>{t.plan.risk_percent}%</td><td><Badge tone={t.status==='CLOSED'?'blue':'green'}>{t.status}</Badge></td><td>{formatDate(t.created_at)}</td><td>{t.status==='PLANNED'?<button className="text-button" onClick={()=>setCloseId(t.id)}>Record outcome <ArrowRight size={14}/></button>:t.outcome?`${t.outcome.result_r} R`:'—'}</td></tr>)}</tbody></table></div>:<Empty icon={<Target/>} title="Your trade log is empty" body="Start with a structured plan. Your decisions will be versioned and preserved here."/>}</Card>
  {closeId&&<div className="modal-shade" onMouseDown={()=>setCloseId('')}><div className="small-modal" onMouseDown={e=>e.stopPropagation()}><div className="modal-top"><h2>Record outcome</h2><button className="icon-btn" onClick={()=>setCloseId('')}><X/></button></div><p className="muted">Manual journal result; this is not broker-reconciled execution accounting.</p><TextField label="Realized P&L (USD)" value={outcome.pnl} onChange={v=>setOutcome(s=>({...s,pnl:v}))}/><TextField label="Result in R" value={outcome.r} onChange={v=>setOutcome(s=>({...s,r:v}))}/><TextArea label="Review notes" value={outcome.notes} onChange={v=>setOutcome(s=>({...s,notes:v}))}/><button className="btn btn-primary full" disabled={busy} onClick={()=>void finalize()}>Save immutable outcome</button></div></div>}
  {formOpen&&<div className="drawer-overlay" onMouseDown={()=>setFormOpen(false)}><div className="drawer" onMouseDown={e=>e.stopPropagation()}><div className="drawer-heading"><div><span className="eyebrow">PLAN BUILDER / V1</span><h2>New trade hypothesis</h2><p>Complete each evidence field to create an auditable plan.</p></div><button className="icon-btn" onClick={()=>setFormOpen(false)}><X size={20}/></button></div><div className="drawer-body">
    <div className="form-subtitle">Market parameters</div><div className="form-grid three"><label className="field"><span>Instrument</span><Select value={plan.symbol} onChange={v=>modify('symbol',v as SymbolName)} options={['EURUSD','GBPUSD','USDJPY','XAUUSD']}/></label><label className="field"><span>Direction</span><Select value={plan.direction} onChange={v=>modify('direction',v as 'BUY'|'SELL')} options={['BUY','SELL']}/></label><label className="field"><span>Timeframe</span><Select value={plan.timeframe} onChange={v=>modify('timeframe',v as Timeframe)} options={['M1','M3','M5','M15','H1','H4','D1']}/></label></div>
    <div className="form-grid"><TextField label="Strategy / model" value={plan.strategy} onChange={v=>modify('strategy',v)}/><label className="field"><span>Session</span><Select value={plan.session} onChange={v=>modify('session',v as Plan['session'])} options={['ASIA','LONDON','NEW_YORK','OTHER']}/></label></div>
    <div className="form-subtitle">Evidence chain <button className="text-button" onClick={()=>setPlan(p=>({...p,...snippets}))}>Fill example wording (edit before saving)</button></div>
    {(['context','observation','evidence','hypothesis','setup','trigger','invalidation'] as const).map((key,index)=><TextArea key={key} label={`${String(index+1).padStart(2,'0')} · ${key.charAt(0).toUpperCase()+key.slice(1)}`} value={plan[key]} onChange={v=>modify(key,v)} placeholder={`Enter ${key} with observed levels, times, or rules` } rows={2}/>)}
    <TextArea label="Management plan" value={plan.management} onChange={v=>modify('management',v)} rows={2}/>
    <div className="form-subtitle">Levels and risk</div><div className="form-grid three"><TextField label="Entry" value={plan.entry} onChange={v=>modify('entry',v)}/><TextField label="Stop" value={plan.stop} onChange={v=>modify('stop',v)}/><TextField label="Target" value={plan.target} onChange={v=>modify('target',v)}/></div>
    <div className="form-grid three"><TextField label="Equity USD" value={plan.equity_usd} onChange={v=>modify('equity_usd',v)}/><TextField label="Risk % (max 2)" value={plan.risk_percent} onChange={v=>modify('risk_percent',v)}/><TextField label="Pip value / lot USD" value={plan.pip_value_per_lot_usd} onChange={v=>modify('pip_value_per_lot_usd',v)}/></div>
    <Notice>The pip value and proposed lot step are illustrative; verify broker contract specifications before any real order.</Notice>
    {risk&&<div className="risk-preview"><div><span>Planned maximum loss</span><strong>${risk.maximum_planned_loss_usd}</strong></div><div><span>Illustrative size</span><strong>{risk.illustrative_lots} lots</strong></div><div><span>Reward/risk</span><strong>1 : {risk.reward_risk}</strong></div></div>}
    {error&&<Notice tone="red">{error}</Notice>}
  </div><div className="drawer-footer"><button className="btn btn-outline" disabled={busy} onClick={()=>void calculate()}><ShieldCheck size={16}/> Calculate Risk</button><button className="btn btn-primary" disabled={busy} onClick={()=>void save()}><FileCheck2 size={16}/> Save Plan <ArrowRight size={16}/></button></div></div></div>}
 </>
}

function AIPage({refresh}:{refresh:()=>void}){
  const formatDate=useTimestampFormat();
 const [trades,setTrades]=useState<Trade[]>([]);const [packages,setPackages]=useState<Package[]>([]);const [reviews,setReviews]=useState<Review[]>([]);const [chosen,setChosen]=useState('');const [input,setInput]=useState('');const [busy,setBusy]=useState(false);const [result,setResult]=useState('');const [error,setError]=useState('');
 const load=useCallback(()=>Promise.all([api.trades(),api.packages(),api.reviews()]).then(([t,p,r])=>{setTrades(t);setPackages(p);setReviews(r);if(!chosen&&t.length)setChosen(t[0].id)}).catch(e=>setError(e.message)),[chosen]);useEffect(()=>{void load()},[load]);
 async function exportPackage(){if(!chosen)return;setBusy(true);setError('');try{const item=await api.createExport(chosen);setResult(`Package ${item.package_id} created. Ready for download.`);window.open(item.download_url,'_blank');await load();refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function importResponse(){setBusy(true);setError('');try{const data=JSON.parse(input) as unknown;const review=await api.importReview(data);setResult(`Imported ${review.id} — ${review.verification}`);setInput('');await load();refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 const example=()=>{const p=packages[0];if(!p)return;setInput(JSON.stringify({schema_version:1,package_id:p.id,trade_decision_id:p.trade_id,plan_version:p.plan_version,review_type:'PRE_TRADE',assessment:'INSUFFICIENT_DATA',findings:[{summary:'Sample observation only: verify spread and completed session structure.',evidence_refs:['PLAN','RISK'],metric_refs:[]}],evidence_refs:['PLAN','RISK'],missing_evidence:['Historical analog records'],contradictions:[],risk_concerns:[],suggested_actions:[],limitations:['Free-text claims require manual verification.']},null,2))};
 return <><PageHeading eyebrow="INTELLIGENCE / FRONTIER AI" title="AI Review Center" subtitle="Export complete evidence. Challenge your thesis in your preferred frontier chat AI. Bring the review back." action={<Badge tone="blue"><CloudOff size={13}/> NO API REQUIRED</Badge>}/>
  <div className="ai-feature"><div className="ai-feature-icon"><BrainCircuit size={28}/></div><div><span className="eyebrow">HUMAN-IN-THE-LOOP INTELLIGENCE</span><h2>Evidence goes out. Critical thinking comes back.</h2><p>Versioned Markdown and JSON packages preserve your plan, rationale, risk calculations and review cutoff.</p></div><div className="ai-feature-path"><span>01 EXPORT</span><ArrowRight size={15}/><span>02 REVIEW</span><ArrowRight size={15}/><span>03 IMPORT</span></div></div>
  {error&&<Notice tone="red">{error}</Notice>}{result&&<Success>{result}</Success>}
  <div className="two-column"><Card><SectionTitle eyebrow="STEP 01" title="Generate review package"/><p className="muted">Choose a plan. The export is frozen at its current version and cutoff.</p><label className="field"><span>Trade decision</span><div className="select-wrap"><select value={chosen} onChange={e=>setChosen(e.target.value)}><option value="">Select trade plan</option>{trades.map(t=><option key={t.id} value={t.id}>{t.id} · {t.symbol} {t.direction} · v{t.version}</option>)}</select></div></label><div className="export-checks"><div><CheckCircle2 size={17}/> Immutable plan snapshot</div><div><CheckCircle2 size={17}/> Local risk calculation</div><div><CheckCircle2 size={17}/> Cutoff-filtered event timeline</div><div><CheckCircle2 size={17}/> Versioned response template</div></div><button className="btn btn-primary full" disabled={!chosen||busy} onClick={()=>void exportPackage()}><Download size={17}/> Generate & Download ZIP</button><Notice tone="blue">Current exports include local plans and risk evidence. Historical analogs are not invented or automatically attached.</Notice></Card>
  <Card><SectionTitle eyebrow="STEP 02 + 03" title="Import frontier AI review"/><p className="muted">Upload the package manually to a chat AI. Paste its structured JSON response here.</p><label className="field"><span>AI response (JSON)</span><textarea className="json-area" value={input} onChange={e=>setInput(e.target.value)} placeholder={'{\n  "package_id": "PKG-...",\n  "assessment": "INSUFFICIENT_DATA",\n  "findings": []\n}'} rows={10}/></label><div className="split-buttons"><button className="btn btn-outline" disabled={!packages.length} onClick={example}>Load example template</button><button className="btn btn-primary" disabled={!input.trim()||busy} onClick={()=>void importResponse()}><Upload size={16}/> Validate & Import</button></div><small className="muted">The importer checks IDs, schema and evidence references, but cannot independently verify free-text claims.</small></Card></div>
  <Card><SectionTitle eyebrow="HISTORY" title="Review packages"/><div className="table-scroll"><table><thead><tr><th>PACKAGE</th><th>TRADE DECISION</th><th>PLAN</th><th>CREATED</th><th>ACTION</th></tr></thead><tbody>{packages.map(p=><tr key={p.id}><td className="mono">{p.id}</td><td className="mono">{p.trade_id}</td><td>v{p.plan_version}</td><td>{formatDate(p.created_at)}</td><td><a className="text-button" href={`/research/api/ai/packages/${p.id}/download`} target="_blank" rel="noreferrer"><Download size={14}/> Download</a></td></tr>)}</tbody></table>{!packages.length&&<Empty icon={<FolderOpen/>} title="No exported packages" body="Export your first trade plan to begin a versioned AI review thread."/>}</div></Card>
  {reviews.length>0&&<Card><SectionTitle eyebrow="REVIEW RESULTS" title="Imported assessments"/><div className="review-list">{reviews.map(r=><div className="review-item" key={r.id}><div><strong>{r.assessment}</strong><small>{r.id} · {formatDate(r.created_at)}</small></div><Badge tone="amber">CONTENT UNVERIFIED</Badge></div>)}</div></Card>}
 </>
}

function StrategyLab(){
  const formatDate=useTimestampFormat();
  const [range,setRange]=useState<CandleRange>();
 const [symbol,setSymbol]=useState<SymbolName>('EURUSD');const [timeframe,setTimeframe]=useState<Timeframe>('M5');const [source,setSource]=useState<SourceName>('MT5');const [result,setResult]=useState<BacktestResult|null>(null);const [error,setError]=useState('');const [loading,setLoading]=useState(false);
 async function run(){setLoading(true);setError('');try{setResult(await api.backtest(symbol,timeframe,source,source.endsWith('_SAVED')?range:undefined))}catch(e){setError((e as Error).message)}finally{setLoading(false)}}
 return <><PageHeading eyebrow="QUANT RESEARCH / LAB" title="Strategy laboratory" subtitle="Reproducible testing without look-ahead or misleading certainty."/><Notice tone="amber">This starter laboratory implements one educational breakout rule. It is not a production tick-accurate backtester and excludes transaction costs.</Notice><div className="two-column lab-layout"><Card><SectionTitle eyebrow="SIMULATION SETUP" title="Research parameters"/><label className="field"><span>Instrument</span><Select value={symbol} onChange={v=>setSymbol(v as SymbolName)} options={['EURUSD','GBPUSD','USDJPY','XAUUSD']}/></label><label className="field"><span>Timeframe</span><Select value={timeframe} onChange={v=>setTimeframe(v as Timeframe)} options={['M1','M3','M5','M15','H1','H4','D1']}/></label><label className="field"><span>Data source</span><Select value={source} onChange={v=>setSource(v as SourceName)} options={['DEMO','MT5','MT5_SAVED','DUKASCOPY_SAVED']}/></label>{source.endsWith('_SAVED')&&<SavedRange range={range} onChange={setRange}/>}<div className="lab-model"><strong>Mechanical baseline</strong><p>Previous-bar breakout → enter at next bar open → 2R target → stop at recent extreme → max 12 bars. Ambiguous exits contribute to expectancy bounds.</p></div><button className="btn btn-primary full" disabled={loading} onClick={()=>void run()}>{loading?<Spinner/>:<Zap size={17}/>} Run historical simulation</button></Card>
 <Card className="lab-results"><SectionTitle eyebrow="RESEARCH OUTPUT" title="Simulation summary"/>{result?<><div className="lab-metrics"><div><span>Eligible signals</span><strong>{result.eligible_signals}</strong></div><div><span>Win rate</span><strong>{result.win_rate==null?'—':result.win_rate+'%'}</strong></div><div><span>Expectancy</span><strong className={result.expectancy_r!=null&&result.expectancy_r>=0?'positive':'negative'}>{result.expectancy_r==null?'—':`${result.expectancy_r} R`}</strong></div><div><span>Ambiguous exits</span><strong>{result.ambiguous_excluded}{result.ambiguous_rate==null?'':` (${result.ambiguous_rate}%)`}</strong></div></div><Badge tone={result.synthetic?'amber':'blue'}>{result.synthetic?'SYNTHETIC · NOT EVIDENCE':'BROKER/DATASET SOURCE'}</Badge><p className="muted small">{result.data_note}</p>{result.ambiguous_excluded>0&&result.expectancy_bounds_r&&<Notice>Expectancy is indeterminate from OHLC. Bounds: {result.expectancy_bounds_r.worst} to {result.expectancy_bounds_r.best} R per signal. Tick replay is required.</Notice>}</>:<Empty icon={<FlaskConical/>} title="No simulation results" body="Choose a dataset and run the mechanical baseline to inspect its limitations."/>}</Card></div>
 {error&&<Notice tone="red">{error}</Notice>}{result&&<Card><SectionTitle eyebrow="SIMULATION DETAILS" title="Recorded sample trades"/><div className="table-scroll"><table><thead><tr><th>ENTRY TIME</th><th>DIRECTION</th><th>ENTRY</th><th>STOP</th><th>TARGET</th><th>RESULT</th></tr></thead><tbody>{result.trades.map((t,i)=><tr key={i}><td>{formatDate(t.entry_time*1000)}</td><td>{t.side}</td><td>{t.entry.toFixed(5)}</td><td>{t.stop.toFixed(5)}</td><td>{t.target.toFixed(5)}</td><td className={t.result_r>=0?'positive':'negative'}>{t.result_r>0?'+':''}{t.result_r} R</td></tr>)}</tbody></table>{!result.trades.length&&<p className="muted">No eligible unambiguous simulated trades.</p>}</div></Card>}
 </>
}

function Journal({refresh}:{refresh:()=>void}){
  const formatDate=useTimestampFormat();
 const [entries,setEntries]=useState<JournalEntry[]>([]);const [title,setTitle]=useState('');const [content,setContent]=useState('');const [kind,setKind]=useState('T');const [error,setError]=useState('');const [message,setMessage]=useState('');const [busy,setBusy]=useState(false);
 const load=useCallback(()=>api.journal().then(setEntries).catch(e=>setError(e.message)),[]);useEffect(()=>{void load()},[load]);
 async function add(){setBusy(true);setError('');try{const e=await api.addJournal(kind,title,content) as {id:string};setMessage(`${e.id} appended to Master Journal`);setTitle('');setContent('');await load();refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <><PageHeading eyebrow="ACADEMY / REFLECTION" title="Master Learning Journal" subtitle="One cumulative history of questions, chart challenges, mistakes, and corrected reasoning." action={<a href="/research/api/journal/download" className="btn btn-secondary"><Download size={16}/> Export Markdown</a>}/>
 <div className="two-column journal-layout"><Card><SectionTitle eyebrow="CAPTURE KNOWLEDGE" title="New journal entry"/><label className="field"><span>Entry category</span><Select value={kind} onChange={setKind} options={['T','Q','CHART','RM','M','R']}/></label><TextField label="Title" value={title} onChange={setTitle} placeholder="What did you learn?"/><TextArea label="Observation / explanation" value={content} onChange={setContent} placeholder="Record the evidence, uncertainty, and what changed in your thinking." rows={7}/><button className="btn btn-primary full" disabled={busy||title.trim().length<2||content.trim().length<5} onClick={()=>void add()}><Plus size={17}/> Append to journal</button>{message&&<Success>{message}</Success>}{error&&<Notice tone="red">{error}</Notice>}</Card>
 <Card><SectionTitle eyebrow="CUMULATIVE TIMELINE" title="Learning history" action={<Badge tone="neutral">{entries.length} entries</Badge>}/><div className="journal-feed">{entries.map(e=><div className="journal-item" key={e.id}><div className="journal-rail"><div className="journal-dot"/></div><div><span className="mono tag-id">{e.id}</span><h4>{e.title}</h4><p>{e.content}</p><small>{formatDate(e.created_at)}</small></div></div>)}{!entries.length&&<Empty icon={<BookOpen/>} title="Build your knowledge base" body="Start a topic (T) or a question (Q); every entry receives a stable ID."/>}</div></Card></div>
 </>
}

function DataLibrary({refresh}:{refresh:()=>void}){
  const formatDate=useTimestampFormat();
 const [datasets,setDatasets]=useState<Dataset[]>([]);const [symbol,setSymbol]=useState<SymbolName>('EURUSD');const [timeframe,setTimeframe]=useState<Timeframe>('M5');const [day,setDay]=useState('2026-11-12');const [hour,setHour]=useState('');const [file,setFile]=useState<File|null>(null);const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [msg,setMsg]=useState('');
 const load=useCallback(()=>api.datasets().then(setDatasets).catch(e=>setError(e.message)),[]);useEffect(()=>{void load()},[load]);
 async function importMT5(){setBusy(true);setError('');try{const d=await api.importMt5(symbol,timeframe,500) as {rows:number};setMsg(`Imported ${d.rows} verified MT5 bars`);await load();refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function importBi5(){if(!file)return;setBusy(true);setError('');try{const d=await api.importBi5(symbol,day,file,hour);setMsg(`Imported ${d.tick_dataset.rows} source ticks, ${d.candle_rows} M5 bars`);await load();refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <><PageHeading eyebrow="STORAGE / PROVENANCE" title="Market data library" subtitle="Maintain separately sourced, checksummed datasets for research and replay." action={<a className="btn btn-secondary" href="/catalog.html">Open fixed-point catalog</a>}/>
 {error&&<Notice tone="red">{error}</Notice>}{msg&&<Success>{msg}</Success>}
 <div className="two-column"><Card><SectionTitle eyebrow="PRIMARY SOURCE" title="Import from MetaTrader 5"/><p className="muted">Read-only import of completed broker bars through the local Windows MT5 terminal.</p><label className="field"><span>Instrument</span><Select value={symbol} onChange={v=>setSymbol(v as SymbolName)} options={['EURUSD','GBPUSD','USDJPY','XAUUSD']}/></label><label className="field"><span>Timeframe</span><Select value={timeframe} onChange={v=>setTimeframe(v as Timeframe)} options={['M1','M3','M5','M15','M30','H1','H4','D1']}/></label><button className="btn btn-primary full" disabled={busy} onClick={()=>void importMT5()}><ArrowDownToLine size={17}/> Import last 500 completed bars</button><small className="muted">Requires MT5 terminal, connected broker account, MetaTrader5 package and pyarrow on Windows.</small></Card>
 <Card><SectionTitle eyebrow="SECONDARY SOURCE" title="Import Dukascopy .bi5"/><p className="muted">Import an existing daily or legacy hourly Dukascopy tick file. No paid bucket access is initiated.</p><label className="field"><span>Source date (UTC)</span><input type="date" value={day} onChange={e=>setDay(e.target.value)}/></label><label className="field"><span>Legacy hourly file? Hour 0–23 (leave blank for daily)</span><input type="number" min="0" max="23" value={hour} onChange={e=>setHour(e.target.value)}/></label><label className="field"><span>Compressed .bi5 file</span><input type="file" accept=".bi5" onChange={e=>setFile(e.target.files?.[0]||null)}/></label><button className="btn btn-secondary full" disabled={!file||busy} onClick={()=>void importBi5()}><Upload size={17}/> Validate & import file</button><small className="muted">Current Dukascopy documented bulk S3 access may incur requester-pays fees. Browser import is free after you obtain a file.</small></Card></div>
 <Card><SectionTitle eyebrow="LOCAL ARCHIVE" title="Verified dataset catalog" action={<Badge tone="blue">{datasets.length} partitions</Badge>}/><div className="table-scroll"><table><thead><tr><th>SOURCE</th><th>INSTRUMENT</th><th>TYPE</th><th>TIMEFRAME</th><th>RECORDS</th><th>IMPORTED</th></tr></thead><tbody>{datasets.map(d=><tr key={d.id}><td><Badge tone="blue">{d.provider}</Badge></td><td><strong>{d.symbol}</strong></td><td>{d.kind}</td><td>{d.timeframe||'tick'}</td><td>{d.rows_count.toLocaleString()}</td><td>{formatDate(d.created_at)}</td></tr>)}</tbody></table>{!datasets.length&&<Empty icon={<Database/>} title="No real datasets imported" body="Add MT5 candles or a Dukascopy .bi5 file to begin persistent historical research."/>}</div></Card>
 </>
}

function SettingsPage(){return <><PageHeading eyebrow="LOCAL ENVIRONMENT / CONFIG" title="Platform settings" subtitle="A minimal, auditable setup that runs on one Windows computer."/><div className="two-column"><Card><SectionTitle eyebrow="RUNTIME" title="Application configuration"/><TimezonePicker/><CandleColorSettings/><div className="settings-list"><div><span>Persistence</span><strong>SQLite (WAL)</strong></div><div><span>Market archive</span><strong>Parquet · Zstandard</strong></div><div><span>Network binding</span><strong>127.0.0.1</strong></div><div><span>AI workflow</span><strong>Manual ZIP / JSON</strong></div><div><span>Order execution</span><Badge tone="green">DISABLED</Badge></div></div></Card><Card><SectionTitle eyebrow="DATA SAFETY" title="Important operational notes"/><div className="settings-notes"><div><ShieldCheck size={18}/><span>Live trading functions are intentionally unavailable.</span></div><div><Archive size={18}/><span>Copy data directory and verify database recovery on a separate storage device.</span></div><div><CloudOff size={18}/><span>Historical research and journal work offline once datasets are imported.</span></div><div><BookOpen size={18}/><span>AI findings are untrusted until checked against source evidence.</span></div></div><a href="http://127.0.0.1:8000/research/docs" className="btn btn-outline full" target="_blank" rel="noreferrer">Open API documentation <ExternalLink size={15}/></a></Card></div></>}

export default App;
