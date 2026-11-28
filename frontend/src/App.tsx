import StatementReconciliation from './StatementReconciliation';
import {JournalPaper} from './PlanPaper';
import {snapshotLane} from './snapshotLanes';
import {type DecisionLocation,SnapshotDetails} from './SnapshotDetails';
import LearningDrill from './LearningDrill';
import {useCallback,useEffect,useLayoutEffect,useMemo,useRef,useState} from 'react';
import { Crosshair, Eraser,   ListFilter,  Minus,  Activity,Archive,ArrowDownToLine,ArrowRight,ArrowUpRight,BarChart3,BookOpen,BrainCircuit,CalendarClock,CheckCircle2,ChevronRight,Clock3,CloudOff,Database,Download,ExternalLink,FileCheck2,FileText,FlaskConical,FolderOpen,LayoutDashboard,Menu,MoreHorizontal,MoveUpRight,Percent,Plus,Radio,RefreshCw,Settings,ShieldCheck,ShieldAlert,Search,Square,Target,TrendingUp,Upload, X, Zap} from 'lucide-react';
import {api} from './api';
import {type ComparisonPlacement,Badge,CandleChart,Card,Empty,Metric,Notice,PriceChange,SectionTitle,Select,Spinner,Success,TextArea,TextField} from './components';
import type {BacktestResult,BacktestRun,BacktestRunSummary,ReplaySelection,ReplaySession,ReplayViewRequest,BrokerSymbol,CandleRange,CandleResponse,ChartSource,Dataset,JournalEntry,JournalContext,Overview,Package,Plan,Review,SourceName,SymbolName,Timeframe,Trade} from './types';
import {SavedRange} from './SavedRange';
import {useBinanceCandles} from './useBinanceCandles';
import './style.css';
import './workspace.css';
import './terminalWorkspace.css';
import {TerminalSurface, ChartJournal, ChartPassEditor} from './TerminalSurface';
import {markNormalizedCandles,useStableCandles} from './candleSeriesSync';
import {TERMINAL_DRAWING_SHORTCUTS,TERMINAL_SHORTCUT_GUIDE,isEditableOrModalElement,resolveTerminalShortcut} from './terminalKeyboard';
import {mergeMt5Candles,subscribeMt5Live,type Mt5TransportMode} from './mt5Stream';
import {TimezonePicker,useTimestampFormat,useTimezone} from './timezone';
import {CandleColorSettings} from './candleColors';
import {useWorkspacePreference} from './useWorkspacePreference';
import {ChartView,frameMinutes,useViewCandles} from './chartViews';
import {useChartIndicator} from './indicators';
import {smtCoverageNotice,smtFeed,useSmt} from './smt';
import {SmtMonitor} from './SmtMonitor';
import {FrozenSmtReceipt} from './FrozenSmtReceipt';
import {ReplayEvidencePanels} from './ReplayEvidencePanels';
import {ArchiveSmtWatch} from './ArchiveSmtWatch';
import {ArrivalPanel} from './ArrivalPanel';
import {ReplayControls, visibleAt} from './ReplayControls';
import {archiveViewIds, FrozenReplayCoverage, ReplayCandleInspector, replayPaneName, type ArchivePane, type ArchiveViewId} from './ArchiveReplay';
import {SignalExplorer,signalSummary,type SignalRecord} from './SignalExplorer';
import {DataTrust} from './DataTrust';
import {CommandPalette,CommandSettings,type CommandTarget} from './CommandPalette';
import {WorkspacePanels,WorkspaceSplitter} from './WorkspacePanels';
import {WorkspacePresets} from './WorkspacePresets';
import {SessionIntelligence} from './SessionIntelligence';
import {SimulationEvidence} from './SimulationEvidence';
import {ChartLink} from './ChartLink';
import {DrawingToolbox,drawingChartProps,usePaneDrawingBooks,type Drawing} from './Drawings';
import {PlanHistory} from './PlanHistory';
import {StrategyBuilder} from './StrategyBuilder';
import type {EvidenceChartLocation} from './StrategyEvaluation';
import {ForexCoverage} from './ForexCoverage';
import {MonthReplay} from './MonthReplay';
import {HistoricalResearch} from './HistoricalResearch';

type ChartLocation = Partial<EvidenceChartLocation> & {indicator?:string;decision?:DecisionLocation['decision'];pairedCoverage?:boolean;sequence:number};

type Section='overview'|'terminal'|'signals'|'plans'|'ai'|'lab'|'builder'|'journal'|'data'|'settings';
const menu:{id:Section;name:string;icon:typeof Activity}[]=[
  {id:'overview',name:'Overview',icon:LayoutDashboard},
  {id:'terminal',name:'Market Terminal',icon:Activity},
  {id:'signals',name:'Signal Explorer',icon:ListFilter},
  {id:'plans',name:'Trade Planner',icon:Target},
  {id:'ai',name:'AI Review Center',icon:BrainCircuit},
  {id:'lab',name:'Strategy Lab',icon:FlaskConical},
  {id:'builder',name:'Strategy Builder',icon:FileCheck2},
  {id:'journal',name:'Learning Journal',icon:BookOpen},
  {id:'data',name:'Data Library',icon:Database},
  {id:'settings',name:'Settings',icon:Settings}
];
const initialPlan:Plan={symbol:'EURUSD',direction:'BUY',strategy:'CRT / Market Structure',session:'LONDON',timeframe:'M5',context:'',observation:'',evidence:'',hypothesis:'',setup:'',trigger:'',invalidation:'',management:'No automatic execution. Manual exit at planned levels.',entry:'',stop:'',target:'',equity_usd:'10000',risk_percent:'0.5',pip_value_per_lot_usd:'10'};
const draftKey='tradeforge.planner.draft.v1';
const revisionKey='tradeforge.planner.revision.v1';
function loadDraft():Plan|null {
  try {
    const saved=JSON.parse(localStorage.getItem(draftKey)??'null') as Plan|null;
    if(saved&&Object.entries(initialPlan).every(([key,value])=>typeof saved[key as keyof Plan]===typeof value)
      &&['EURUSD','GBPUSD','USDJPY','XAUUSD'].includes(saved.symbol))return saved;
  }catch{/* Invalid drafts require a new plan. */}
  return null;
}
const snippets={context:'H1 structure and session trend; verify higher timeframe bias.',observation:'M5 price swept a prior session extreme and re-entered the range.',evidence:'Document exact candle times and levels; no historical edge established.',hypothesis:'If reclaim holds, next impulse may reach opposite range liquidity.',setup:'Candidate range and sweep: unconfirmed until close.',trigger:'Wait for M5 close back inside the reference range.',invalidation:'Structure breaks beyond the sweep with a completed candle.'};
function App(){
  const {zone}=useTimezone();
  const [section,setSection]=useWorkspacePreference<Section>('page','overview',menu.map(item=>item.id));const [sidebar,setSidebar]=useState(false);const [reload,setReload]=useState(0);
  const [selectedSignal,selectSignal]=useState<SignalRecord>();
  const [terminalPlan,setTerminalPlan]=useState(false);
  const [incomingPlan,setIncomingPlan]=useState<Plan>();
  const [journalSignal,setJournalSignal]=useState<SignalRecord>();
  const [journalContext,setJournalContext]=useState<JournalContext>();
  function journalFrom(context:JournalContext,signal?:SignalRecord){
    setJournalLocation(undefined);setJournalContext(context);setJournalSignal(signal);go('journal');
  }
  const [reviewPlan,setReviewPlan]=useState("");
  const [planLocation,setPlanLocation]=useState<{id:string;version?:number}>();
  const [journalLocation,setJournalLocation]=useState<JournalEntry>();
  const [dataLocation,setDataLocation]=useState<Dataset>();
  const [chartLocation,setChartLocation]=useState<ChartLocation>();
  const [indicatorCommands,setIndicatorCommands]=useState<{id:string;label:string}[]>([]);
  useEffect(()=>{
    let active=true;
    api.get<{id:string;label:string}[]>('/indicators').then(values=>{if(active)setIndicatorCommands(values);}).catch(()=>{});
    return()=>{active=false;};
  },[]);
  const openChart=(location:Omit<ChartLocation,'sequence'>)=>{
    setChartLocation(previous=>({...location,sequence:(previous?.sequence??0)+1}));go('terminal');
  };

  const [overview,setOverview]=useState<Overview|null>(null);const [overviewLoading,setOverviewLoading]=useState(true);const [backendError,setBackendError]=useState('');
  const refresh=useCallback(()=>setReload(v=>v+1),[]);
  useEffect(()=>{let mounted=true;setOverviewLoading(true);api.overview().then(x=>{if(mounted){setOverview(x);setBackendError('')}}).catch(e=>{if(mounted)setBackendError(e.message)}).finally(()=>{if(mounted)setOverviewLoading(false)});return()=>{mounted=false}},[reload]);
  const go=(s:Section)=>{setSection(s);setSidebar(false);setTerminalPlan(false)};
  function planFromChart(seed:Partial<Plan>) {
    if(loadDraft()&&!window.confirm('Replace the unsaved local draft with this chart context?'))return;
    try{localStorage.removeItem(revisionKey);}catch{}
    setIncomingPlan({...initialPlan,...seed});
    if(section==='terminal')setTerminalPlan(true);else go('plans');
  }
  async function openSignal(reference:import('./types').SignalReference) {
    const record=await api.get<SignalRecord>(`/indicators/signals/${reference.event_key}?status=${reference.status}`);
    selectSignal(record);go('signals');
  }
  async function openCommand(target:CommandTarget){
    if(target.type==='signal'&&target.status){await openSignal({event_key:target.id,status:target.status});return;}
    if(target.type==='plan'){setPlanLocation({id:target.id});go('plans');return;}
    if(target.type==='journal'){
      setJournalLocation(await api.get<JournalEntry>('/journal/entries/'+encodeURIComponent(target.id)));go('journal');return;
    }
    if(target.type==='dataset'){
      setDataLocation(await api.get<Dataset>('/datasets/'+encodeURIComponent(target.id)));go('data');
    }
  }
  const planner=<Planner refresh={refresh} openSignal={openSignal} initialLocation={planLocation} onPassSaved={()=>go('journal')} onLesson={(plan_reference,snapshot_id)=>journalFrom({plan_reference,snapshot_id})} incoming={incomingPlan} onConsume={()=>setIncomingPlan(undefined)} onReview={id=>{setReviewPlan(id);go("ai");}}/>;
  const [railOpen,setRailOpen]=useState(false);
  return <div className={'app-shell '+(section==='terminal'?'terminal-shell '+(railOpen?'nav-wide':'nav-rail'):'')}>
    <aside className={'sidebar '+(sidebar?'side-open':'')}>
      <div className="brand"><div className="brand-mark"><TrendingUp size={20} strokeWidth={2.5}/></div><div><strong>Trade<span>Forge</span></strong><small>RESEARCH TERMINAL</small></div></div>
      <div className="workspace-name"><div className="workspace-avatar">TF</div><div className="workspace-text"><strong>Personal Workspace</strong><span>Local desktop environment</span></div><ChevronRight size={15} className="faint"/></div>
      <div className="nav-category">WORKSPACE</div><nav>{menu.slice(0,6).map(m=><button className={'nav-item '+(section===m.id?'selected':'')} key={m.id} aria-label={m.name} title={m.name} onClick={()=>go(m.id)}><m.icon size={18} strokeWidth={1.8}/><span>{m.name}</span>{m.id==='ai'&&<span className="tiny-pulse"/>}</button>)}</nav>
      <div className="nav-category nav-category-second">LIBRARY & CONFIG</div><nav>{menu.slice(6).map(m=><button className={'nav-item '+(section===m.id?'selected':'')} key={m.id} aria-label={m.name} title={m.name} onClick={()=>go(m.id)}><m.icon size={18} strokeWidth={1.8}/><span>{m.name}</span></button>)}</nav>
      <div className="sidebar-bottom">
        <div className="risk-safety"><ShieldCheck size={18}/><div><strong>Execution Locked</strong><p>No automated orders enabled</p></div></div>
        <div className="profile"><div className="profile-avatar">U</div><div><strong>Local Trader</strong><span>Single-user edition</span></div><MoreHorizontal size={18}/></div>
      </div>
    </aside>
    {sidebar&&<button className="mobile-shade" aria-label="Close navigation" onClick={()=>setSidebar(false)}/>}
    <main className="main" tabIndex={0}>
      <header className="topbar">
        <div className="topbar-start"><button className="icon-btn mobile-menu" onClick={()=>setSidebar(true)} aria-label="Open navigation"><Menu size={21}/></button>{section==='terminal'&&<button className="terminal-nav-switch icon-btn" aria-label="Expand workspace navigation" aria-expanded={railOpen} onClick={()=>setRailOpen(value=>!value)}><Menu size={18}/></button>}<div className="breadcrumb">Workspace <ChevronRight size={15}/> <strong>{menu.find(m=>m.id===section)?.name}</strong></div></div>
        <div className="topbar-end"><CommandPalette onTarget={openCommand} commands={[
          ...menu.map(item=>({id:item.id,label:item.name,run:()=>go(item.id)})),
          ...watchSymbols.map(symbol=>({id:'instrument:'+symbol,label:'Open '+symbol,run:()=>openChart({symbol})})),
          ...indicatorCommands.map(item=>({id:'indicator:'+item.id,label:'Add '+item.label,run:()=>openChart({indicator:item.id})})),
        ]}/><div className="topbar-mode"><span className="dot green"/> LOCAL MODE</div><div className="topbar-sep"/><span className="topbar-date"><CalendarClock size={16}/> {new Date().toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric',timeZone:zone})}</span><button className="icon-btn" aria-label="Refresh data" onClick={refresh}><RefreshCw size={17}/></button><div className="top-avatar">U</div></div>
      </header>
      {backendError&&<div className="error-banner" role="alert"><CloudOff size={18}/><span>API offline: {backendError}. Start the API using <code>start-api.ps1</code>.</span><button type="button" className="small-tool" onClick={refresh}>Retry</button></div>}
      <div className="page-content" key={section}>
      {section==='overview'&&<Dashboard overview={overview} loading={overviewLoading} error={backendError} onRetry={refresh} go={go}/>}
      {section==='terminal'&&<Terminal expanded onJournalReturn={openChart} onLeaveDecision={()=>setChartLocation(undefined)} onPlan={planFromChart} onSignal={openSignal} onChartLesson={snapshot_id=>journalFrom({snapshot_id})} initialLocation={chartLocation}/>}
      {section==='signals'&&<SignalExplorer selected={selectedSignal} onSelect={selectSignal} onOpenPlan={(id,version)=>{setPlanLocation({id,version});go("plans");}} onJournal={(signal,snapshot_id)=>journalFrom({snapshot_id},signal)} onPlan={(signal,snapshot_id)=>{
        const {primary,timeframe}=signal.provenance;
        planFromChart({symbol:primary.symbol as SymbolName,timeframe,strategy:'SMT research',snapshot_id,knowledge_cutoff_utc:snapshot_id?undefined:new Date(signal.transitions.at(-1)!.time*1000).toISOString(),
          context:`${primary.source} · ${primary.feed_id} · ${primary.price_basis}`,
          evidence:signalSummary(signal),signal_reference:{event_key:signal.event_key,status:signal.status}});
      }}/>}
      {(section==='plans'||section==='terminal'&&terminalPlan)&&
        (section==='terminal'?<TerminalSurface title="Trade plan" active close={()=>setTerminalPlan(false)}>
          {planner}
        </TerminalSurface>:planner)}
      {section==='ai'&&<AIPage refresh={refresh} initialTrade={reviewPlan}/>}
      {section==='lab'&&<div className="strategy-builder"><MonthReplay/><HistoricalResearch openChart={openChart}/><StrategyLab/></div>}
      {section==='builder'&&<StrategyBuilder openChart={openChart}/>}
      {section==='journal'&&<Journal onReturn={openChart} refresh={refresh} initialEntry={journalLocation} incoming={journalSignal} initialContext={journalContext}
        clearLink={()=>{setJournalSignal(undefined);setJournalContext(undefined);}}
        openPlan={(id,version)=>{setPlanLocation({id,version});go('plans');}}
        openSignal={openSignal}/>}
      {section==='data'&&<><DataLibrary refresh={refresh} initialDataset={dataLocation} openReplay={range=>openChart({symbol:'EURUSD',timeframe:'M5',source:'DUKASCOPY_SAVED',range,pairedCoverage:true})}/><StatementReconciliation/></>}
      {section==='settings'&&<SettingsPage/>}
      </div>
      <footer className="footer"><span>TRADEFORGE • RESEARCH ENVIRONMENT • SINGLE USER</span><span>Charts by <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView</a> · UTC storage · {zone}</span></footer>
    </main>
  </div>
}

function PageHeading({eyebrow,title,subtitle,action}:{eyebrow:string;title:string;subtitle:string;action?:React.ReactNode}){return <div className="page-heading"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{subtitle}</p></div>{action&&<div className="page-heading-actions">{action}</div>}</div>}

const watchSymbols:SymbolName[]=['EURUSD','GBPUSD','USDJPY','XAUUSD'];
const chartSources:ChartSource[]=['MT5','MT5_SAVED','DUKASCOPY_SAVED','BINANCE_SPOT','DEMO'];
const chartTimeframes:Timeframe[]=['M1','M3','M5','M15','M30','H1','H4','D1'];
type ChartLevel=import('./components').ChartLevel;
type ReplayLoadKind='quick'|'archive'|'resume';
const archiveReplayLimit=5000;
const evidenceBookmarksKey='tradeforge.workspace.evidence-bookmarks.v1';
type EvidenceBookmark={
  id:string; session_id:string; source:ChartSource; symbol:string; timeframe:Timeframe;
  position:number; view:ArchiveViewId; instrument:string; candle_timeframe:Timeframe;
  time:number; cutoff:number; created_at:string;
};
function loadEvidenceBookmarks():EvidenceBookmark[]{
  try{
    const parsed:unknown=JSON.parse(localStorage.getItem(evidenceBookmarksKey)??'[]');
    if(Array.isArray(parsed))return parsed.filter((item):item is EvidenceBookmark=>
      Boolean(item&&typeof item.id==='string'&&typeof item.session_id==='string'&&typeof item.time==='number'&&typeof item.cutoff==='number')).slice(0,25);
  }catch{}
  return [];
}
function Terminal({expanded=false,onPlan,onSignal,initialLocation,onChartLesson,onLeaveDecision,onJournalReturn}:{onJournalReturn?:(location:DecisionLocation)=>void;onLeaveDecision?:()=>void;expanded?:boolean;onPlan?:(seed:Partial<Plan>)=>void;
  onSignal?:(reference:import('./types').SignalReference)=>Promise<void>; initialLocation?:ChartLocation;onChartLesson?:(snapshot:string)=>void}){
  const [strip,setStrip]=useState<HTMLDivElement|null>(null);
  const [mobileControls,setMobileControls]=useState(false);
  const [surface,setSurface]=useState<'layers'|'sessions'|'decision'|'inspector'>();
  const [journalVisible,setJournalVisible]=useState(false);
  const [journalRevision,setJournalRevision]=useState(0);
  const captureButton=useRef<HTMLButtonElement>(null);
  const terminalClock=useTimestampFormat();
  const [planPrices,setPlanPrices]=useState<string[]>([]);
  const [captureState,setCaptureState]=useState<{
    generation:object; saving:boolean; id?:string; status?:string;
  }>();
  const liveCaptureGeneration=useRef<object|null>(null);
  const captureSequence=useRef(0);
  const {zone}=useTimezone();
  const [forexSymbol,setSymbol]=useWorkspacePreference<SymbolName>('forex-symbol','EURUSD',watchSymbols);
  const [timeframe,setTimeframe]=useWorkspacePreference<Timeframe>('timeframe','M5',chartTimeframes);
  const [source,setSource]=useWorkspacePreference<ChartSource>('source','MT5',chartSources);
  const [selectedRange,setRange]=useState<CandleRange|undefined>(initialLocation?.range);
  const savedSource=source==='MT5_SAVED'||source==='DUKASCOPY_SAVED';
  const isBinance=source==='BINANCE_SPOT';
  const [brokerSymbol,setBrokerSymbol]=useWorkspacePreference<string>('broker-symbol','EURUSD');
  const symbol=isBinance?'BTCUSDT':source==='MT5'?brokerSymbol:forexSymbol;
  const [comparisonPreference,setComparisonSymbol]=useWorkspacePreference<string>('chart-comparison','auto');
  const comparisonSymbol=comparisonPreference==='auto'?
    (symbol==='EURUSD'&&timeframe==='M5'&&(savedSource||source==='MT5')?'GBPUSD':'none'):comparisonPreference;
  const range=useMemo(()=>{
    if(!savedSource)return undefined;
    if(selectedRange?.coverage_selection && (source!=='DUKASCOPY_SAVED'||forexSymbol!=='EURUSD'||
        timeframe!=='M5'||comparisonSymbol!=='GBPUSD')){
      return {start:selectedRange.start,end:selectedRange.end};
    }
    return selectedRange;
  },[savedSource,selectedRange,source,forexSymbol,timeframe,comparisonSymbol]);
  const exactEvidence=Boolean(initialLocation?.evidencePath&&initialLocation.symbol===forexSymbol&&
    initialLocation.timeframe===timeframe&&initialLocation.source===source&&initialLocation.range===range);
  useEffect(()=>{
    if(!initialLocation?.symbol)return;
    if(initialLocation.source)setSource(initialLocation.source);
    else if(source==='BINANCE_SPOT')setSource('MT5');
    if(initialLocation.timeframe)setTimeframe(initialLocation.timeframe);
    if(initialLocation.range)setRange(initialLocation.range);
    if(initialLocation.pairedCoverage)setComparisonSymbol('GBPUSD');
    setBrokerSymbol(initialLocation.symbol);
    setSymbol(initialLocation.symbol);
  },[initialLocation]);
  const [instruments,setInstruments]=useState<BrokerSymbol[]>([]);
  const [symbolQuery,setSymbolQuery]=useState('');
  const [symbolError,setSymbolError]=useState('');
  const [pollError,setPollError]=useState('');
  const [sessionZones,setSessionZones]=useState<Record<string,Drawing>>({});
  const updateZone=useCallback((id:string,drawing?:Drawing)=>{
    setSessionZones(current=>{
      if(drawing)return {...current,[id]:drawing};
      if(!current[id])return current;
      const next={...current};delete next[id];return next;
    });
  },[]);
  const digits=instruments.find(item=>item.name===symbol)?.digits ?? (isBinance?2:symbol==='EURUSD'||symbol==='GBPUSD'?5:3);
  const [data,setData]=useState<CandleResponse|null>(null);
  const [error,setError]=useState('');const [busy,setBusy]=useState(true);
  const [refreshCount,setRefreshCount]=useState(0);
  const [viewCount,setViewCount]=useWorkspacePreference('chart-views','1',['1','2','3','4']);
  const [link]=useState(()=>new ChartLink());
  const [linkCrosshair,setLinkCrosshair]=useWorkspacePreference('link-crosshair','off',['on','off']);
  const [linkRange,setLinkRange]=useWorkspacePreference('link-range','off',['on','off']);
  const [linkZoom,setLinkZoom]=useWorkspacePreference('link-zoom','off',['on','off']);
  const [linkInstrument,setLinkInstrument]=useWorkspacePreference('link-instrument','off',['on','off']);
  const [linkTimeframe,setLinkTimeframe]=useWorkspacePreference('link-timeframe','off',['on','off']);
  const [linkReplay,setLinkReplay]=useWorkspacePreference('link-replay','on',['on','off']);
  link.options={crosshair:linkCrosshair==='on',range:linkRange==='on',zoom:linkZoom==='on'};

  const [showSmt,setShowSmt]=useWorkspacePreference('show-smt','on',['on','off']);
  const [smtWidth,setSmtWidth]=useWorkspacePreference<string>('smt-width','3');
  const [smtAlignment,setSmtAlignment]=useWorkspacePreference<string>('smt-alignment','0');
  const [smtTolerance,setSmtTolerance]=useWorkspacePreference<string>('smt-tolerance','0.5');
  const [smtConfirmation,setSmtConfirmation]=useWorkspacePreference<string>('smt-confirmation','1');
  const [placement,setPlacement]=useWorkspacePreference<ComparisonPlacement>('comparison-placement','Overlay',['Overlay','New pane']);
  const [levels,setLevels]=useState<ChartLevel[]>([]);
  const [levelHistory,setLevelHistory]=useState<{undo:ChartLevel[][];redo:ChartLevel[][]}>({undo:[],redo:[]});
  const [replayIndex,setReplayIndex]=useState<number|null>(null);
  const [loadedReplay,setLoadedReplay]=useState<{generation:object;session:ReplaySession}>();
  const [replaySelection,setReplaySelection]=useState<ReplaySelection>();
  const [evidenceBookmarks,setEvidenceBookmarks]=useState<EvidenceBookmark[]>(loadEvidenceBookmarks);
  const [bookmarkStatus,setBookmarkStatus]=useState('');
  const [pendingRestore,setPendingRestore]=useState<EvidenceBookmark>();
  const [replayBusy,setReplayBusy]=useState<ReplayLoadKind>();
  const [replayError,setReplayError]=useState<{kind:ReplayLoadKind;message:string}>();
  const replayLoadSequence=useRef(0);
  const replayPending=useRef(false);
  const [savedReplays,setSavedReplays]=useState<{id:string;position:number;updated_at:string}[]>([]);
  const replayListSequence=useRef(0);
  const [quotes,setQuotes]=useState<Record<string,number|null>>({});
  const [mt5Transport,setMt5Transport]=useState<Mt5TransportMode>('idle');
  const replayActiveRef=useRef(false);
  const indicatorScope=JSON.stringify([source,symbol,timeframe,range]);
  const primaryLoadGeneration=useMemo(()=>({}),
    [symbol,timeframe,source,expanded,refreshCount,range,initialLocation,exactEvidence,pendingRestore]);
  const replaySession=loadedReplay?.generation===primaryLoadGeneration ? loadedReplay.session : undefined;
  const replayData=replaySession?.market;
  const replayActive=replayIndex!==null&&Boolean(replayData);
  const replayArchive=replayActive&&Boolean(replaySession?.request?.window_bars);
  const invalidateReplayLoad=useCallback(()=>{
    replayLoadSequence.current++;
    replayPending.current=false;
    setReplayBusy(undefined);
    setReplayError(undefined);
  },[]);
  useLayoutEffect(()=>{
    invalidateReplayLoad();
    return ()=>{
      replayLoadSequence.current++;
      replayPending.current=false;
    };
  },[primaryLoadGeneration,viewCount,comparisonSymbol,linkReplay,linkInstrument,linkTimeframe,invalidateReplayLoad]);
  const [loadedPrimaryGeneration,setLoadedPrimaryGeneration]=useState<object>();
  const [loadedIndicatorScope,setLoadedIndicatorScope]=useState("");
  const refreshCandles=useCallback(()=>setRefreshCount(n=>n+1),[]);
  const live=useBinanceCandles(isBinance,timeframe,refreshCandles);
  const historical=useMemo(()=>data?.source===source?data.candles:[],[data,source]);
  const liveRows=live?.candles ?? [];
  const candles=isBinance&&liveRows.length
    ? [...new Map([...historical,...liveRows].map(row=>[row.time,row])).values()].sort((a,b)=>a.time-b.time).slice(-600)
    : historical;
  const replayLinked=replayArchive||linkReplay==='on';
  const replayStep=frameMinutes[timeframe]*60;
  const archiveCutoff=replayArchive&&replayIndex!==null ? replayData?.replay_cutoffs?.[replayIndex] : undefined;
  const rawVisibleCandles=useMemo(()=> {
    if (!replayData||replayIndex===null)return candles;
    return archiveCutoff===undefined ? markNormalizedCandles(replayData.candles.slice(0,replayIndex+1))
      : visibleAt(replayData.candles,archiveCutoff,replayStep);
  },[replayData,replayIndex,candles,archiveCutoff,replayStep]);
  const visibleCandles=useStableCandles(rawVisibleCandles);
  const cutoff=archiveCutoff??(replayActive?visibleCandles.reduce((known,bar)=>
    Math.max(known,bar.time+replayStep,bar.available_at??0),0):undefined);
  const companionScope=(view:'2'|'3'|'4')=>{
    const pane=replayArchive?replaySession?.views[view]:undefined;
    return {scope:pane&&'candles' in pane?`${source}:${pane.symbol}:${pane.timeframe}`:'',
      candles:pane&&'candles' in pane?pane.candles:undefined,step:pane?frameMinutes[pane.timeframe]*60:replayStep};
  };
  const drawingBooks=usePaneDrawingBooks(cutoff,{scope:expanded?`${source}:${symbol}:${timeframe}`:'',
    candles:replayArchive?replayData?.candles:undefined,step:replayStep},
    companionScope('2'),companionScope('3'),companionScope('4'));
  const drawingBook=drawingBooks.primary;
  const {interaction,setInteraction}=drawingBook;
  const picking=interaction.mode==='plan';
  const picked=picking?interaction.prices:planPrices;
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
    setBusy(true);setError('');setPollError('');setData(null);setReplayIndex(null);setLoadedReplay(undefined);setReplaySelection(undefined);setPlanPrices([]);
    setInteraction(current=>current.mode==='plan'?{mode:'navigate'}:current);
    const decision=pendingRestore && pendingRestore.symbol===symbol && pendingRestore.source===source && pendingRestore.timeframe===timeframe
      ? {session_id:pendingRestore.session_id,position:pendingRestore.position,view:pendingRestore.view,time:pendingRestore.time}
      : initialLocation?.decision;
    if(decision&&(pendingRestore||(initialLocation&&symbol===initialLocation.symbol&&source===initialLocation.source&&timeframe===initialLocation.timeframe))){
      api.replay(decision.session_id).then(session=>{
        if(!active)return;
        setLoadedReplay({generation:primaryLoadGeneration,session});
        setReplaySelection({view:decision.view,time:decision.time});
        setReplayIndex(decision.position);
        if(pendingRestore)setPendingRestore(undefined);
      }).catch(e=>{if(active)setError(e.message);}).finally(()=>{if(active)setBusy(false);});
      return ()=>{active=false;};
    }
    (exactEvidence?api.get<CandleResponse>(initialLocation!.evidencePath!):
      api.candles(symbol,timeframe,source,expanded?600:200,source==='MT5',range))
      .then(main=>{if(active){setData(main);setLoadedIndicatorScope(indicatorScope);setLoadedPrimaryGeneration(primaryLoadGeneration);}})
      .catch(e=>{if(active){setError(e.message);setData(null)}})
      .finally(()=>{if(active)setBusy(false)});
    return()=>{active=false};
  },[symbol,timeframe,source,expanded,refreshCount,range,initialLocation,exactEvidence,primaryLoadGeneration]);
  useEffect(()=>{
    replayActiveRef.current=replayIndex!==null;
  },[replayIndex]);
  useEffect(()=>{
    if(source!=='MT5'||busy||error||replayIndex!==null){
      setMt5Transport('idle');
      return;
    }
    let active=true;
    const stop=subscribeMt5Live({
      symbol,
      timeframe,
      limit:10,
      includeLatest:true,
      quoteSymbols:expanded?watchSymbols:[],
      onCandles:update=>{
        if(!active||replayActiveRef.current)return;
        setPollError('');
        setData(current=>{
          const next=mergeMt5Candles(current,update,expanded?600:200);
          return next===undefined?current:next;
        });
      },
      onQuotes:next=>{
        if(!active||replayActiveRef.current)return;
        setQuotes(next);
      },
      onError:message=>{
        if(!active||replayActiveRef.current)return;
        setPollError(message);
      },
      onTransportChange:mode=>{
        if(active)setMt5Transport(mode);
      },
    });
    return()=>{active=false;stop();};
  },[source,symbol,timeframe,busy,error,replayIndex,expanded]);
  useEffect(()=>{
    if(!expanded){setQuotes({});return;}
    if(source!=='DEMO'){if(source!=='MT5')setQuotes({});return;}
    setQuotes({});
    let active=true;
    void Promise.all(watchSymbols.map(async s=>{
      try{const q=await api.quote(s,'DEMO');return [s,q.bid] as const}
      catch{return [s,null] as const}
    })).then(entries=>{if(active)setQuotes(Object.fromEntries(entries))});
    return()=>{active=false};
  },[expanded,source]);
  useEffect(()=>{
    if(!expanded)return;
    setLevelHistory({undo:[],redo:[]});
    try{
      const raw=window.localStorage.getItem(`tf-levels:${source}:${symbol}:${timeframe}`);
      const arr=raw?JSON.parse(raw):[];
      setLevels(Array.isArray(arr)?arr.filter((v:ChartLevel)=>typeof v.price==='number'&&Number.isFinite(v.price)).slice(0,50):[]);
    }catch{setLevels([])}
  },[expanded,source,symbol,timeframe]);
  const writeLevels=(next:ChartLevel[])=>{
    setLevels(next);
    try{window.localStorage.setItem(`tf-levels:${source}:${symbol}:${timeframe}`,JSON.stringify(next));}catch{/* Browser storage unavailable */}
  };
  const changeLevels=(next:ChartLevel[])=>{
    setLevelHistory({undo:[...levelHistory.undo,levels].slice(-25),redo:[]});
    writeLevels(next);
  };
  const addLevel=(price:number)=>{
    if(!Number.isFinite(price)||levels.length>=50)return;
    changeLevels([...levels,{id:crypto.randomUUID(),price:Number(price.toFixed(digits))}]);
    setInteraction({mode:'navigate'});
  };
  const clearLevels=()=>{if(window.confirm('Remove all horizontal levels from this chart?'))changeLevels([]);};
  const undoLevels=()=>{
    const next=levelHistory.undo.at(-1);if(!next)return;
    setLevelHistory({undo:levelHistory.undo.slice(0,-1),redo:[...levelHistory.redo,levels]});writeLevels(next);
  };
  const redoLevels=()=>{
    const next=levelHistory.redo.at(-1);if(!next)return;
    setLevelHistory({undo:[...levelHistory.undo,levels],redo:levelHistory.redo.slice(0,-1)});writeLevels(next);
  };
  const indicatorCandles=!replayActive&&data?.latest_unconfirmed?visibleCandles.slice(0,-1):visibleCandles;
  const last=visibleCandles.at(-1);const first=visibleCandles[0];
  const change=last&&first?(last.close-first.open)/first.open*100:0;
  const canReplay=expanded&&!isBinance&&!busy&&!error&&Boolean(data&&data.candles.length>(data.latest_unconfirmed?1:0));
  const replayOptions=useCallback(()=>{
    if(!expanded||isBinance)return;
    const sequence=++replayListSequence.current;
    api.replays(symbol,timeframe,source).then(value=>{
      if(replayListSequence.current===sequence)setSavedReplays(value);
    }).catch(()=>{if(replayListSequence.current===sequence)setSavedReplays([]);});
  },[symbol,timeframe,source,expanded,isBinance]);
  useLayoutEffect(()=>{
    setSavedReplays([]);
    replayOptions();
    return ()=>{replayListSequence.current++;};
  },[replayOptions]);
  async function loadReplay(kind:ReplayLoadKind,id?:string){
    if(replayPending.current)return;
    const sequence=++replayLoadSequence.current;
    const isCurrent=()=>replayLoadSequence.current===sequence;
    replayPending.current=true;
    setReplayBusy(kind);setReplayError(undefined);
    try{
      const views:ReplayViewRequest[]=[];
      if(compare)views.push({view:'comparison',symbol:compare,timeframe});
      if(kind==='archive'||linkReplay==='on')for(let number=2;number<=Number(viewCount);number++){
        const preference=(key:string)=>{try{return localStorage.getItem('tradeforge.workspace.'+key);}catch{return null;}};
        const saved=preference(`view-${number}-symbol`)??'';
        const selectedFrame=preference(number===2?'secondary-timeframe':`view-${number}-frame`) as Timeframe|null;
        const frame=selectedFrame&&chartTimeframes.includes(selectedFrame)?selectedFrame:number===2?'H1':number===3?'M15':'H4';
        views.push({view:String(number),symbol:linkInstrument==='on'||!options.includes(saved)?symbol:saved,
          timeframe:linkTimeframe==='on'?timeframe:frame});
      }
      const session=id?await api.replay(id):await api.startReplay(symbol,timeframe,source,views,range,
        kind==='archive'?archiveReplayLimit:undefined,compare&&validRules?rules:undefined);
      if(!isCurrent())return;
      if(!session.market.candles.length)throw new Error('No completed replay candles in this range.');
      if(kind==='archive'&&(session.request?.window_bars!==archiveReplayLimit||!session.market.replay_cutoffs?.length)){
        throw new Error('The API did not return the archive replay window. Restart the API and try again.');
      }
      const lastIndex=(session.market.replay_cutoffs?.length??session.market.candles.length)-1;
      setLoadedReplay({generation:primaryLoadGeneration,session});
      setReplaySelection(session.checkpoint?.selected??undefined);
      setReplayIndex(session.checkpoint?.position??(session.request?.window_bars ? lastIndex : Math.floor(lastIndex*.6)));
    }catch(failure){
      if(isCurrent())setReplayError({kind,message:(failure as Error).message});
    }finally{
      if(isCurrent()){
        replayPending.current=false;
        setReplayBusy(undefined);
      }
    }
  }
  const archivePanes=useMemo<ArchivePane[]>(()=>{
    if(!replayArchive||!replaySession)return [];
    return ([...archiveViewIds,'comparison'] as const).flatMap(view=>{
      const data=view==='primary'?{...replaySession.market,symbol,timeframe}:replaySession.views[view];
      return data?[{view,data}]:[];
    });
  },[replayArchive,replaySession,symbol,timeframe]);
  useEffect(()=>{
    if(replayArchive&&initialLocation?.decision&&window.matchMedia('(max-width: 800px)').matches)setSurface('inspector');
  },[replayArchive,initialLocation?.sequence]);
  const chartViewCount=replayArchive ? Math.max(1,...archivePanes.map(({view})=>view==='primary'||view==='comparison'?1:Number(view))) : Number(viewCount);
  const inspectedPane=archivePanes.find(({view})=>view===replaySelection?.view)??archivePanes[0];
  const drawingView=inspectedPane?.view??'primary';
  const activeBook=drawingView==='comparison'?undefined:drawingBooks[drawingView];
  const drawingDestination=inspectedPane&&`${replayPaneName(drawingView)} · ${inspectedPane.data.symbol} ${inspectedPane.data.timeframe}`;
  // Cancel an unfinished gesture when the operator leaves its pane. Committed writes
  // still resolve against the originating book and chart market.
  useLayoutEffect(()=>()=>activeBook?.setEdit(undefined),[drawingView,activeBook?.setEdit]);
  const inspectionFeed=inspectedPane&&'candles' in inspectedPane.data ? inspectedPane.data : undefined;
  const inspectedTime=replaySelection&&replaySelection.view===inspectedPane?.view ? replaySelection.time : undefined;
  const inspectionCandles=useMemo(()=>{
    if(!inspectedPane||!inspectionFeed||archiveCutoff===undefined)return [];
    if(inspectedPane.view==='primary')return visibleCandles;
    return visibleAt(inspectionFeed.candles,archiveCutoff,frameMinutes[inspectionFeed.timeframe]*60);
  },[inspectedPane,inspectionFeed,archiveCutoff,visibleCandles]);
  const selectedVisibleTime=inspectedTime!==undefined&&inspectionCandles.some(bar=>bar.time===inspectedTime)?inspectedTime:undefined;
  const selectInspectionPane=useCallback((view:ArchiveViewId)=>{
    const pane=archivePanes.find(item=>item.view===view)?.data;
    if(!pane||'error' in pane||archiveCutoff===undefined)return;
    const bar=visibleAt(pane.candles,archiveCutoff,frameMinutes[pane.timeframe]*60).at(-1)??pane.candles[0];
    if(bar)setReplaySelection({view,time:bar.time});
  },[archivePanes,archiveCutoff]);
  const toggleLevel=useCallback(()=>{
    if(levels.length>=50)return;
    if(drawingView!=='primary')selectInspectionPane('primary');
    setInteraction(current=>({mode:current.mode==='level'?'navigate':'level'}));
  },[levels.length,drawingView,selectInspectionPane,setInteraction]);
  const primaryDrawings=useMemo(()=>[...drawingBook.items,...Object.values(sessionZones)
    .filter(zone=>drawingBook.eligible(zone.anchors))],[drawingBook.items,drawingBook.eligible,sessionZones]);
  const primaryProps=drawingChartProps(drawingBook,digits,drawingView==='primary');
  const indicatorFeed=replayActive?replayData:data;
  const indicatorCutoff=cutoff??Math.min(Math.floor(Date.now()/1000),Math.max(
    (indicatorCandles.at(-1)?.time??0)+frameMinutes[timeframe]*60,indicatorCandles.at(-1)?.available_at??0));
  const indicator=useChartIndicator(expanded&&!isBinance&&(replayActive||!busy&&!error&&indicatorScope===loadedIndicatorScope),indicatorCandles,source,timeframe,
    symbol,indicatorCutoff,indicatorFeed??undefined,replayActive,initialLocation);
  const options=[...new Set([symbol,...(isBinance?[]:source==='MT5'?(instruments.length?instruments.map(item=>item.name):watchSymbols):watchSymbols)])];
  const returningToDecision=initialLocation?.decision&&initialLocation.symbol===symbol&&initialLocation.source===source&&initialLocation.timeframe===timeframe;
  const compare=replaySession?replaySession.views.comparison?.symbol??'':
    !returningToDecision&&expanded&&comparisonSymbol!==symbol&&options.includes(comparisonSymbol)?comparisonSymbol:'';
  const frozenComparison=replaySession?.views.comparison;
  const comparison=useViewCandles(compare,timeframe,source,refreshCount,cutoff,true,range,frozenComparison);
  const feedIdentity=(feed:CandleResponse|null|undefined)=>feed&&[
    feed.feed_id,feed.price_basis,feed.dataset_id,feed.partitions?.map(part=>[part.id,part.sha256]),
  ];
  const frozenPlacement=replaySession&&initialLocation?.decision?.session_id===replaySession.id?
    initialLocation.decision.comparison_placement:undefined;
  const displayedPlacement=frozenPlacement??placement;
  const decisionTime=inspectedTime===undefined?inspectionCandles.at(-1)?.time:selectedVisibleTime;
  const decisionReplay=replayArchive&&replaySession&&replayIndex!==null&&inspectedPane&&decisionTime!==undefined?
    {session_id:replaySession.id,position:replayIndex,view:inspectedPane.view,time:decisionTime,...(compare?{comparison_placement:displayedPlacement}:{})}:undefined;
  const captureKey=JSON.stringify([symbol,timeframe,source,range,refreshCount,expanded,
    exactEvidence?initialLocation:undefined,feedIdentity(isBinance?undefined:indicatorFeed),
    compare,feedIdentity(comparison.feed),Boolean(comparison.candles?.length),placement,chartViewCount,
    replayActive,replaySession?.id,replayIndex,cutoff,decisionReplay]);
  const canSaveCapture=(!replayArchive||Boolean(decisionReplay))&&(replayActive||loadedPrimaryGeneration===primaryLoadGeneration&&!busy&&!error)&&!replayBusy&&
    (Boolean(decisionReplay)||!compare||(savedSource?Boolean(comparison.feed&&comparison.candles?.length):Boolean(comparison.feed||comparison.error)));
  const captureGeneration=useMemo(()=>({}),[captureKey,primaryLoadGeneration,canSaveCapture]);
  useLayoutEffect(()=>{
    liveCaptureGeneration.current=canSaveCapture?captureGeneration:null;
    return ()=>{liveCaptureGeneration.current=null};
  },[canSaveCapture,captureGeneration]);
  const currentCapture=canSaveCapture&&captureState?.generation===captureGeneration?captureState:undefined;
  const chartCapture=currentCapture?.id;
  async function saveChartEvidence(png:string) {
    if(liveCaptureGeneration.current!==captureGeneration)return;
    const sequence=++captureSequence.current;
    const isCurrent=()=>liveCaptureGeneration.current===captureGeneration&&captureSequence.current===sequence;
    setCaptureState({generation:captureGeneration,saving:true,id:chartCapture});
    try {
      const lanes=!decisionReplay&&savedSource&&(compare||(cutoff!=null&&indicatorFeed?.feed_id==='dukascopy-bi5-v2'))?[
        snapshotLane('primary',symbol,timeframe,indicatorFeed??undefined,visibleCandles),
        ...(compare?[snapshotLane('comparison',compare,timeframe,comparison.feed,comparison.candles??[])]:[]),
      ]:undefined;
      const replayLanes:('primary'|'comparison')[]=['primary'];
      if(compare&&comparison.candles?.length)replayLanes.push('comparison');
      const saved=await api.saveSnapshot(png,symbol,timeframe,source,cutoff,undefined,lanes,decisionReplay,
        decisionReplay?replayLanes:undefined);
      if(isCurrent())setCaptureState({generation:captureGeneration,saving:false,id:saved.id,status:'Chart capture saved'});
    } catch(error) {
      if(isCurrent())setCaptureState({generation:captureGeneration,saving:false,id:chartCapture,
        status:error instanceof Error?error.message:'Chart capture could not be saved'});
    }
  }
  const bookmarkEvidence=useCallback(()=>{
    if(!replayActive||!replaySession||replayIndex===null)return;
    const targetView=inspectedPane?.view??'primary';
    const targetInstrument=inspectedPane?.data.symbol??symbol;
    const targetFrame=inspectedPane?.data.timeframe??timeframe;
    const targetTime=decisionTime??visibleCandles.at(-1)?.time;
    const targetCutoff=cutoff??(targetTime!==undefined?targetTime+frameMinutes[targetFrame]*60:undefined);
    if(targetTime===undefined||targetCutoff===undefined)return;
    const nextSelection=replaySelection??{view:targetView,time:targetTime};
    const mergedBookmarks=[...new Set([...(replaySession.checkpoint?.bookmarks??[]),replayIndex])].sort((a,b)=>a-b);
    const entry:EvidenceBookmark={
      id:`${replaySession.id}:${replayIndex}:${targetView}:${targetTime}`,
      session_id:replaySession.id,source,symbol,timeframe,position:replayIndex,
      view:targetView,instrument:targetInstrument,candle_timeframe:targetFrame,
      time:targetTime,cutoff:targetCutoff,created_at:new Date().toISOString(),
    };
    setEvidenceBookmarks(prev=>{
      const updated=[entry,...prev.filter(item=>item.id!==entry.id)].slice(0,25);
      try{localStorage.setItem(evidenceBookmarksKey,JSON.stringify(updated));}catch{}
      return updated;
    });
    setBookmarkStatus(`Bookmarked ${targetInstrument} ${targetFrame} (${replayPaneName(targetView)}) · candle ${terminalClock(targetTime*1000)} · cutoff ${terminalClock(targetCutoff*1000)}`);
    void api.saveCheckpoint(replaySession.id,replayIndex,mergedBookmarks,nextSelection)
      .then(()=>replayOptions())
      .catch(reason=>setBookmarkStatus(reason instanceof Error?reason.message:'Bookmark could not be saved'));
    if(canSaveCapture&&!chartCapture)captureButton.current?.click();
  },[replayActive,replaySession,replayIndex,inspectedPane,symbol,timeframe,decisionTime,visibleCandles,cutoff,replaySelection,source,terminalClock,replayOptions,canSaveCapture,chartCapture]);
  useEffect(()=>{
    if(!expanded)return;
    const onKey=(e:KeyboardEvent)=>{
      const {insideModal,insideEditable,hasOpenModalDialog}=isEditableOrModalElement(e.target);
      const focusedView=link.focused(drawingView)??'primary';
      const focusedPaneFrozen=replayActive||(focusedView!=='primary'&&linkTimeframe==='on');
      const canDraw=Boolean(activeBook&&!activeBook.saving&&!activeBook.conflict&&activeBook.current&&activeBook.current.items.length<100);
      const action=resolveTerminalShortcut({
        defaultPrevented:e.defaultPrevented,repeat:e.repeat,key:e.key,
        altKey:e.altKey,ctrlKey:e.ctrlKey,metaKey:e.metaKey,shiftKey:e.shiftKey,
        insideModal,insideEditable,hasOpenModalDialog,replayActive,focusedPaneFrozen,canDraw,
      });
      if(!action)return;
      if(action.type==='escape'){
        activeBook?.setInteraction(current=>current.mode==='navigate'?current:{mode:'navigate'});
        return;
      }
      e.preventDefault();
      if(action.type==='level')toggleLevel();
      else if(action.type==='fit')link.fitFocused(drawingView);
      else if(action.type==='bookmark')bookmarkEvidence();
      else if(action.type==='tool')activeBook?.begin(action.tool);
      else if(action.type==='timeframe'){
        if(focusedView==='primary')setTimeframe(action.timeframe);
        else link.setFocusedTimeframe(action.timeframe,focusedView);
      }
    };
    window.addEventListener('keydown',onKey);return()=>window.removeEventListener('keydown',onKey);
  },[expanded,activeBook,drawingView,link,linkTimeframe,replayActive,toggleLevel,bookmarkEvidence,setTimeframe]);
  const pinnedRules=replaySession?.request?.smt_rules??replaySession?.signal_rules;
  const rules=pinnedRules??{period:Number(smtWidth),alignment_bars:Number(smtAlignment),
    tolerance_bps:Number(smtTolerance),confirmation_bars:Number(smtConfirmation)};
  const ruleInputs=[
    ['Swing width',String(pinnedRules?.period??smtWidth),setSmtWidth,2,20,1],
    ['UTC match (bars)',String(pinnedRules?.alignment_bars??smtAlignment),setSmtAlignment,0,10,1],
    ['Tolerance (bps)',String(pinnedRules?.tolerance_bps??smtTolerance),setSmtTolerance,0,100,0.1],
    ['Confirmation bars',String(pinnedRules?.confirmation_bars??smtConfirmation),setSmtConfirmation,0,20,1],
  ] as const;
  const validRules=ruleInputs.every(([,value,,min,max,step])=>value!==''&&Number(value)>=min&&
    Number(value)<=max&&(step!==1||Number.isInteger(Number(value))));
  const firstFeed=smtFeed(symbol,replayData??(data?.source===source?data:undefined));
  const secondFeed=smtFeed(compare,comparison.feed);
  const missingSmtFeed=source!=='DEMO'&&Boolean(compare)&&Boolean(data&&comparison.feed)&&(!firstFeed||!secondFeed);
  const nativeSmtPair=replayArchive&&replayData&&frozenComparison&&'candles' in frozenComparison ?
    [replayData,frozenComparison] : [];
  const smtPartitionLimit=nativeSmtPair.some(feed=>(feed.partitions?.length??0)>50);
  const smtUnaligned=nativeSmtPair.some(({coverage})=>coverage&&
    (coverage.start%replayStep!==0||coverage.end%replayStep!==0));
  const legacySmtLimit=showSmt==='on' ? (nativeSmtPair.some(feed=>feed.candles.length>2000) ?
    'SMT overlay unavailable. Each archive must contain at most 2,000 candles.' :
    smtPartitionLimit ? 'SMT overlay unavailable. Each archive must contain at most 50 source partitions.' :
    smtUnaligned ? 'SMT overlay unavailable. Archive boundaries must align with the chart timeframe.' : undefined) : undefined;
  const strictSmtLimit=smtPartitionLimit ? 'Strict SMT unavailable. Each archive must contain at most 50 source partitions.' :
    nativeSmtPair.some(({coverage})=>coverage&&coverage.end-coverage.start>10000*replayStep) ?
      'Strict SMT unavailable. Each archive must span at most 10,000 UTC candle slots.' :
      smtUnaligned ? 'Strict SMT unavailable. Archive boundaries must align with the chart timeframe.' : undefined;
  const smt=useSmt(indicatorCandles,comparison.candles,timeframe,showSmt==='on'&&!legacySmtLimit&&Boolean(compare)&&(replayActive||!busy&&!error)&&validRules&&
    (source==='DEMO'||Boolean(firstFeed&&secondFeed)),rules,firstFeed,secondFeed,replayActive,refreshCount,frameMinutes[timeframe]*60);
  const peerTimes=useMemo(()=>new Set(comparison.candles?.map(bar=>bar.time)),[comparison.candles]);
  const missingPeer=showSmt==='on'&&compare&&comparison.candles&&rules.alignment_bars===0&&indicatorCandles.find(bar=>!peerTimes.has(bar.time));
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
  const latestNotice=!replayActive&&data?.latest_unconfirmed?(mt5Transport==='sse'?'Latest MT5 bar · close unconfirmed · live stream':mt5Transport==='polling'?'Latest MT5 bar · close unconfirmed · polling fallback':'Latest MT5 bar · close unconfirmed'):'';
  const resumeReplay=savedReplays.length>0&&<label>Resume saved replay <select value="" disabled={Boolean(replayBusy)}
    onChange={event=>{if(event.target.value)void loadReplay('resume',event.target.value);}}>
    <option value="">Choose checkpoint</option>{savedReplays.map(item=><option key={item.id} value={item.id}>
      {new Date(item.updated_at).toLocaleString()} · replay step {item.position+1}
    </option>)}
  </select></label>;
  const resumeFeedback=<>
    {replayBusy==='resume'&&<span role="status">Loading saved replay…</span>}
    {replayError?.kind==='resume'&&<span role="alert">{replayError.message}</span>}
  </>;
  const replayControls=replayActive&&replayData&&replaySession&&<ReplayControls key={replaySession.id}
    pauseRequested={Boolean(surface)} candles={replayData.candles} cutoffs={replayData.replay_cutoffs} index={replayIndex!} onSeek={setReplayIndex}
    onExit={()=>{if(initialLocation?.decision)onLeaveDecision?.();setReplayIndex(null);setLoadedReplay(undefined);setReplaySelection(undefined);}}
    onCheckpoint={async(position,bookmarks)=>{
      await api.saveCheckpoint(replaySession.id,position,bookmarks,replayArchive?replaySelection:undefined);
      replayOptions();
    }} initialBookmarks={replaySession.checkpoint?.bookmarks} step={replayStep}/>;
  if(!expanded)return <Card className="market-card">
    <div className="market-header"><div className="market-title"><div className="pair-icon"><Activity size={19}/></div><div><div className="market-pair-row"><h3>{pairText}</h3><Badge tone={source==='DEMO'?'amber':'blue'}>{source==='DEMO'?'SIMULATED':source}</Badge></div><p>Market context · research environment</p></div></div><div className="price-head">{last?<><strong>{last.close.toFixed(digits)}</strong><PriceChange value={change}/></>:<strong>—</strong>}</div></div>
    <div className="chart-toolbar"><div className="timeframe-buttons">{(['M1','M3','M5','M15','M30','H1','H4','D1'] as Timeframe[]).map(t=><button key={t} className={timeframe===t?'on':''} onClick={()=>setTimeframe(t)}>{t}</button>)}</div><div className="chart-actions">{!isBinance&&source!=='MT5'&&<Select value={forexSymbol} options={watchSymbols} onChange={s=>setSymbol(s as SymbolName)}/>}<Select label="Market data source" value={source} options={chartSources} onChange={s=>setSource(s as ChartSource)}/></div>{brokerPicker}</div>
    <div className="chart-frame">{busy?<div className="chart-loading"><Spinner/> Loading candles</div>:error?<div className="chart-loading error-text"><CloudOff/> {error}</div>:candles.length?<CandleChart candles={candles} height={300} symbol={symbol} precision={digits}/>:<Empty title="No candles" body="Connect MT5 or import a validated dataset."/>}</div>
    <div className="chart-footer"><span>{label} {latestNotice} {pollError}</span><span>{candles.length} candles · {zone}</span></div>
  </Card>;
  const decisionSymbol=inspectedPane?.data.symbol??symbol;
  const primaryDecision=drawingView==='primary';
  const decisionFrame=inspectedPane?.data.timeframe??timeframe;
  const decisionFeed=inspectedPane?inspectionFeed:indicatorFeed;
  const openTime=decisionTime;
  const companionAtOpen=compare && openTime!==undefined && ['primary','comparison'].includes(inspectedPane?.view??'')
    ? (inspectedPane?.view==='comparison'?visibleCandles:comparison.candles)?.find(bar=>bar.time===openTime) : undefined;
  const counterpart=inspectedPane?.view==='comparison'?symbol:compare;
  const closeSurface=()=>setSurface(undefined);
  function inspectSection(id:string){
    if(id==='terminal-journal'){setJournalVisible(true);setJournalRevision(value=>value+1);}
    if(window.matchMedia('(max-width: 800px)').matches)setSurface('inspector');
    requestAnimationFrame(()=>document.getElementById(id)?.focus({preventScroll:false}));
  }
  return <div className="terminal-suite">
    <div className="terminal-suite-header"><h1>Market Terminal</h1><span className="terminal-hint"><ShieldCheck size={14}/> Local research</span></div>
    <WorkspacePanels><div className="terminal-layout">
      <section className="terminal-core" data-mobile-controls={mobileControls}>
        <div className="terminal-identity"><div className="terminal-pair"><div className="pair-badge">{symbol.slice(0,3)}</div><div><strong>{pairText}</strong><span>{label}</span><small>{latestNotice} {pollError}</small></div></div><div className="terminal-head-price">{last?<><strong>{last.close.toFixed(digits)}</strong><PriceChange value={change}/></>:<span>—</span>}</div></div>
        <button className="workspace-overflow" aria-expanded={mobileControls} onClick={()=>setMobileControls(!mobileControls)}>Chart settings · {timeframe}</button>
        <div className="terminal-controls"><div className="timeframe-buttons">{(['M1','M3','M5','M15','M30','H1','H4','D1'] as Timeframe[]).map(t=><button key={t} className={timeframe===t?'on':''} onClick={()=>setTimeframe(t)}>{t}</button>)}</div><div className="terminal-control-right">{expanded&&!replayActive&&<button className="small-tool" onClick={()=>{
          setSymbol('EURUSD');setTimeframe('M5');setComparisonSymbol('GBPUSD');setSmtAlignment('0');setShowSmt('on');setSource('DUKASCOPY_SAVED');
        }}>Forex M5 workspace</button>}<Select label="Market data source" value={source} options={chartSources} onChange={s=>setSource(s as ChartSource)}/><Select label="Chart layout" value={String(chartViewCount)} options={['1','2','3','4']} disabled={replayArchive} onChange={value=>setViewCount(value as typeof viewCount)}/><button className="small-tool" onClick={()=>setRefreshCount(n=>n+1)} title="Refresh candles"><RefreshCw size={16}/></button></div>{brokerPicker}</div>
        {last&&<div className="session-strip" aria-label="Session hours at chart time" ref={setStrip}/>}
        <div className="terminal-action-strip">
          <button disabled={!indicator.controls} title={!indicator.controls?'Indicator evidence requires a supported chart feed with loaded candles':undefined} onClick={()=>setSurface('layers')}>Indicators &amp; layers</button>
          <button disabled={!last} onClick={()=>setSurface('sessions')}>Sessions</button>
          <button onClick={()=>inspectSection('terminal-evidence')}>Inspect evidence</button>
          <button className="decision-trigger" onClick={()=>setSurface('decision')}>Record decision</button>
        </div>
        {activeBook&&<div className="terminal-drawing-tools"><DrawingToolbox key={drawingView} book={activeBook} destination={drawingDestination}/></div>}
        <div className="terminal-chart-zone"><div className="chart-tool-rail"><button className={'rail-tool '+((!activeBook||activeBook.interaction.mode==='navigate')?'active':'')} title="Crosshair (Esc)" onClick={()=>activeBook?.setEdit(undefined)}><Crosshair size={18}/></button><button className={'rail-tool '+(interaction.mode==='level'?'active':'')} title="Place horizontal level (Alt+H)" disabled={levels.length>=50} onClick={toggleLevel}><Minus size={19}/></button><div className="rail-separator"/>{([['trend','Draw Trendline','t',TrendingUp],['ray','Draw Ray','r',MoveUpRight],['fib','Draw Fibonacci retracement','g',Percent],['rectangle','Draw Rectangle','b',Square],['risk-reward','Draw Risk/reward','p',Target]] as const).map(([kind,title,shortcut,Icon])=>{
          const armed=activeBook?.interaction.mode==='place'&&activeBook.interaction.draft.kind===kind;
          const disabled=!activeBook||activeBook.saving||activeBook.conflict||!activeBook.current||activeBook.current.items.length>=100;
          return <button key={kind} type="button" className={'rail-tool '+(armed?'active':'')} aria-label={title} aria-keyshortcuts={shortcut} aria-pressed={armed} title={`${title} (${shortcut.toUpperCase()})`} disabled={disabled} onClick={()=>activeBook?.begin(kind)}><Icon size={17}/></button>;
        })}<div className="rail-separator"/><button className="rail-tool" title="Remove all saved levels" disabled={levels.length===0} onClick={clearLevels}><Eraser size={18}/></button><span className="rail-count">{levels.length}</span></div>
        <div className={'terminal-chart-columns '+(chartViewCount>1?'multi-chart':'')}>
          <div className="primary-chart" role="region" aria-label="Primary chart" data-inspecting={inspectedPane?.view==='primary'||undefined}><div className="pane-label"><span className="dot green"/>{symbol} · {timeframe} <span className="muted">{replayActive?'REPLAY':exactEvidence?'FROZEN STRATEGY EVIDENCE':'PRICE'}</span></div>{!replayActive&&busy?<div className="chart-loading"><Spinner/> Loading candles</div>:!replayActive&&error?<div className="chart-loading error-text"><CloudOff/> {error}</div>:visibleCandles.length?<CandleChart key={`${symbol}-${timeframe}-${source}`} candles={visibleCandles} focus={exactEvidence&&!replayArchive&&initialLocation?.timestamp!==undefined?
            {epoch:initialLocation.timestamp,sequence:initialLocation.sequence}:undefined} link={link} linkId="primary" height={chartViewCount>1?360:510} onSetTimeframe={replayActive?undefined:setTimeframe}
            replayCandleTime={inspectedPane?.view==='primary'||inspectedPane?.view==='comparison'?selectedVisibleTime:undefined}
            onReplayCandleSelect={replayArchive?time=>setReplaySelection({view:'primary',time}):undefined} timeframe={timeframe} comparison={compare&&comparison.candles?.length?{symbol:compare,candles:comparison.candles,precision:precisionFor(compare),placement:displayedPlacement,fixedPlacement:Boolean(frozenPlacement),onPlacementChange:setPlacement,smt:replayActive?smt.events?.filter(event=>event.confirmed_at!==null):smt.events,smtWindowOnly:savedSource&&replayActive}:undefined} symbol={symbol} precision={digits} indicators={indicator.data} levels={levels} drawings={primaryDrawings}
            {...primaryProps} drawingPrompt={picking?`Select ${['entry','stop','target'][picked.length]}`:primaryProps.drawingPrompt}
            captureButtonRef={captureButton} onSaveSnapshot={canSaveCapture?saveChartEvidence:undefined} savingSnapshot={currentCapture?.saving??false} snapshotStatus={currentCapture?.status}
            onSmtSelect={!replayActive&&onSignal?event=>{
              if(event.event_key)void onSignal({event_key:event.event_key,status:event.status}).catch(reason=>setPollError(reason.message));
            }:undefined}
            onAddLevel={interaction.mode==='plan'?price=>{
              const prices=[...interaction.prices,price.toFixed(digits)];
              if(prices.length===3){setPlanPrices(prices);setInteraction({mode:'navigate'});}
              else setInteraction({mode:'plan',prices});
            }:interaction.mode==='level'?addLevel:undefined} showSnapshot showDataLegend/>:<Empty title="Market data unavailable" body="Connect MetaTrader 5 or import saved MT5 / Dukascopy candles."/>}</div>
          {archiveViewIds.slice(1,chartViewCount).map(view=>{
            const inspecting=inspectedPane?.view===view;
            return <ChartView key={view} number={Number(view)}
              mainSymbol={symbol} source={source} options={options} refresh={refreshCount} cutoff={replayLinked?cutoff:undefined} precision={precisionFor} range={range}
              frozen={replayActive&&replayLinked?(replaySession?.views[view]??
                {symbol,timeframe,error:'This pane was not captured in the replay snapshot'}):undefined}
              link={link} linkedFrame={linkTimeframe==='on'?timeframe:undefined} linkedSymbol={linkInstrument==='on'?symbol:undefined}
              drawingBook={replayArchive?drawingBooks[view]:undefined} inspecting={inspecting} selectedCandle={inspecting?selectedVisibleTime:undefined}
              onCandleSelect={replayArchive?time=>setReplaySelection({view,time}):undefined} onConfigurationChange={invalidateReplayLoad}/>;
          })}
        </div></div>
        {replayArchive&&<div className="archive-replay-transport">{replayControls}</div>}
        <div className="terminal-replay">
          {!replayArchive&&(replayActive?replayControls:<div className="research-controls">
            <button className="small-tool" onClick={()=>void loadReplay('quick')} disabled={!canReplay||Boolean(replayBusy)}
              title={savedSource&&range?'Quick replay uses at most 600 candles':undefined}><Clock3 size={17}/> Bar replay</button>
            {!savedSource&&resumeReplay}
            {!savedSource&&resumeFeedback}
            {replayBusy==='quick'&&<span role="status">Loading bar replay…</span>}
            {replayError?.kind==='quick'&&<span role="alert">{replayError.message}</span>}
          </div>)}
          <div className="terminal-footer-info">{zone} · {visibleCandles.length} bars · {(replayData??data)?.price_basis||'unverified'}</div>
        </div>
        {levels.length>0||levelHistory.undo.length>0||levelHistory.redo.length>0?<details className="data-trust">
          <summary>Horizontal levels ({levels.length})</summary><div className="research-controls">
            <button className="small-tool" disabled={!levelHistory.undo.length} onClick={undoLevels}>Undo level edit</button>
            <button className="small-tool" disabled={!levelHistory.redo.length} onClick={redoLevels}>Redo level edit</button>
            {levels.map(level=><button className="small-tool" key={level.id}
              onClick={()=>changeLevels(levels.filter(item=>item.id!==level.id))}>Remove {level.price.toFixed(digits)}</button>)}
          </div></details>:null}

        {chartViewCount>1&&<details className="data-trust"><summary>Chart synchronization</summary>
          <div className="research-controls">{([
            ['Crosshair',linkCrosshair,setLinkCrosshair],['Time range',linkRange,setLinkRange],['Zoom',linkZoom,setLinkZoom],
            ['Instrument',linkInstrument,setLinkInstrument],['Timeframe',linkTimeframe,setLinkTimeframe],
            ['Replay',linkReplay,setLinkReplay],
          ] as const).map(([name,value,update])=><label key={name}><input type="checkbox" checked={name==='Replay'?replayLinked:value==='on'}
            disabled={name==='Replay'&&replayActive} onChange={event=>update(event.target.checked?'on':'off')}/>{name}</label>)}</div>
        </details>}
        {!replayArchive&&range?.coverage_selection&&<p>Coverage chart feed · {range.coverage_selection.feed_id}</p>}
        {savedSource&&!replayArchive&&<SavedRange range={range} onChange={next=>setRange(next && next.start===range?.start && next.end===range?.end ? range : next)}
          onFreeze={()=>void loadReplay('archive')} freezeBusy={Boolean(replayBusy)} onEdit={invalidateReplayLoad}/>}
        {!replayActive&&savedSource&&(resumeReplay||replayBusy==='archive'||replayBusy==='resume'||
          replayError?.kind==='archive'||replayError?.kind==='resume')&&<div className="research-controls archive-replay-status">
          {resumeReplay}
          {resumeFeedback}
          {replayBusy==='archive'&&<span role="status">Freezing selected range…</span>}
          {replayError?.kind==='archive'&&<span role="alert">{replayError.message}</span>}
        </div>}
        {!replayArchive&&data?.coverage&&<div className="indicator-controls" role="status">
          {data.coverage.gaps.length} UTC coverage gaps · {data.coverage.duplicate_bars} duplicate bars · {data.coverage.truncated?'Showing latest 600 candles':'Requested range loaded'}
        </div>}
        <TerminalSurface title="Indicators & layers" active={surface==='layers'} close={closeSurface} inline>
          {indicator.controls}
        </TerminalSurface>
        <TerminalSurface title="Session settings" active={surface==='sessions'} close={closeSurface} inline>
        {last&&<SessionIntelligence rows={indicatorCandles} step={frameMinutes[timeframe]*60}
          cutoff={cutoff??Math.min(Date.now()/1000,(indicatorCandles.at(-1)?.time??last.time)+frameMinutes[timeframe]*60)} digits={digits} onZone={updateZone} strip={strip}/>}
        </TerminalSurface>
      </section>
      <WorkspaceSplitter />
      <TerminalSurface title="Inspector" active={surface==='inspector'} close={closeSurface} inline>
        <aside className="terminal-dock" aria-label="Chart workspace inspector">
          <nav aria-label="Inspector views">{['Evidence','Watchlist','Provenance','Journal'].map(name=><button key={name}
            onClick={()=>inspectSection('terminal-'+name.toLowerCase())}>{name}</button>)}</nav>
          <section id="terminal-evidence" tabIndex={-1}><h2>Evidence</h2>
          {replayActive&&<div className="evidence-bookmark-bar"><button type="button" className="small-tool" onClick={bookmarkEvidence}>Bookmark candle (E)</button>{bookmarkStatus&&<small role="status">{bookmarkStatus}</small>}</div>}
          {!replayActive&&bookmarkStatus&&<small role="status">{bookmarkStatus}</small>}
          {evidenceBookmarks.length>0&&<details className="data-trust"><summary>Evidence bookmarks ({evidenceBookmarks.length})</summary>{evidenceBookmarks.map(item=><div key={item.id} className="evidence-bookmark-row"><span><strong>{item.instrument} {item.candle_timeframe}</strong> · {replayPaneName(item.view)} · {terminalClock(item.time*1000)}</span><button type="button" className="small-tool" onClick={()=>{setSource(item.source as ChartSource);setSymbol(item.symbol as SymbolName);setTimeframe(item.timeframe as Timeframe);setPendingRestore(item);}}>Open</button></div>)}</details>}
          {cutoff!=null&&<p className="cutoff-readout">Replay cutoff <time dateTime={new Date(cutoff*1000).toISOString()}>{terminalClock(cutoff*1000)} · {zone}</time></p>}
        {inspectedPane&&<ReplayCandleInspector panes={archivePanes} pane={inspectedPane} candles={inspectionCandles}
          selectedTime={inspectedTime} precision={precisionFor(inspectedPane.data.symbol)} onView={selectInspectionPane}
          onCandle={time=>setReplaySelection({view:inspectedPane.view,time})}/>}
          {counterpart&&openTime!==undefined&&['primary','comparison'].includes(inspectedPane?.view??'')&&<p className="peer-readout">
            {counterpart} · same UTC open <strong>{companionAtOpen?companionAtOpen.close.toFixed(precisionFor(counterpart)):comparison.error??'Missing candle at this open'}</strong></p>}
          {openTime!==undefined&&inspectedPane?.view==='primary'&&indicator.data.length>0&&<details className="data-trust">
            <summary>Indicator evidence at selected open</summary>
            {indicator.data.map(result=><section key={result.id}><strong>{result.id}</strong>
              {Object.entries(result.series).map(([name,points])=>{
                const point=points.find(item=>item.time===openTime);
                return <p key={name}>{name} · {point?`${point.value} · ${point.status}`:'No published point at this open'}</p>;
              })}
              {result.warnings.length>0&&<p>{result.warnings.join(' · ')}</p>}
              {result.coverage&&<p>{result.coverage.confirmation_status} · {result.coverage.status}</p>}
            </section>)}
          </details>}
          <div className="indicator-controls comparison-controls"><label>SMT comparison
          <Select label="SMT comparison" value={compare||'none'} options={['none',...options.filter(item=>item!==symbol)]} onChange={setComparisonSymbol}/>
          </label>{compare&&<label><input className="smt-toggle" type="checkbox" checked={showSmt==='on'}
            onChange={event=>setShowSmt(event.target.checked?'on':'off')}/> Mark SMT</label>}
          {compare&&showSmt==='on'&&<details><summary>SMT rules</summary><div className="smt-rules">
            {ruleInputs.map(([label,value,setValue,min,max,step])=><label key={label}>{label}
              <input type="number" value={value} disabled={Boolean(pinnedRules)} min={min} max={max} step={step} onChange={event=>setValue(event.target.value)}/>
            </label>)}
            {replayActive&&!pinnedRules&&<small>Legacy replay · workspace rules are shown because this session did not record SMT settings.</small>}
            {!validRules&&<small role="status">Enter values within the displayed limits.</small>}
          </div></details>}
          {compare&&showSmt==='on'&&<button type="button" className="small-tool"
            disabled={!validRules||busy||replayActive} onClick={refreshCandles}
            title={replayActive?'Exit replay to refresh market data':undefined}>
            <RefreshCw size={15}/> Refresh SMT data
          </button>}
          <SmtMonitor watch={source==='MT5'&&compare&&validRules&&!replayActive
            ? {primary_symbol:symbol,comparison_symbol:compare,timeframe,...rules} : undefined}
            strict={compare&&validRules&&(source==='MT5_SAVED'||source==='DUKASCOPY_SAVED')
              ? {primary:replayActive||!busy&&indicatorScope===loadedIndicatorScope ? firstFeed : undefined,
                comparison:replayActive&&!replaySession?.views.comparison ? undefined : secondFeed,
                timeframe,cutoff:indicatorCutoff,rules,unavailable:strictSmtLimit} : undefined}/>
          <small role="status">{legacySmtLimit || smt.error || comparison.error || smtCoverageNotice(smt.coverage) || (smt.authority==='VERIFIED_MT5_CAPTURE'?'SMT · verified MT5 candles':smt.authority==='VERIFIED_SAVED_DATASET'?'SMT · verified archive evidence':smt.authority==='CLIENT_SUPPLIED_UNVERIFIED'?'SMT preview · unverified candle input':undefined) || (missingSmtFeed ? 'SMT requires an identified feed and price basis for both instruments' : compare ? busy ? 'Refreshing chart data…' : smt.pending ? 'Recalculating SMT…' : comparison.candles?.length ? `${compare} · closed candles` : 'Waiting for comparison candles' : 'Compare instruments on the same chart')}</small>
          {missingPeer?<small role="status">Missing peer candle · {compare} · {new Date(missingPeer.time*1000).toISOString()}</small>:
            smt.events&&!smtCoverageNotice(smt.coverage)&&<small role="status">{smt.events.length?
              `${smt.events.length} SMT divergence${smt.events.length===1?'':'s'} in paired candles`:
              'No SMT divergence in the paired candles at this cutoff'}</small>}
          {!replayActive&&smt.alert?.confirmed_at&&<strong role="status">{(smt.pending || smt.error || smtCoverageNotice(smt.coverage)) ? 'Saved SMT_CONFIRMED' : 'SMT_CONFIRMED'} · {smt.alert.direction} · {symbol} / {compare} · {new Date(smt.alert.confirmed_at*1000).toISOString()}</strong>}
          {!replayActive&&smt.alert?.event_key&&onSignal&&<button className="small-tool" onClick={()=>void onSignal({
            event_key:smt.alert!.event_key!,status:'SMT_CONFIRMED'
          }).catch(reason=>setPollError(reason.message))}>Inspect alert evidence</button>}
        </div>
        {source === 'DUKASCOPY_SAVED' && cutoff != null && replaySession?.market.source === source &&
          (replayArchive||loadedPrimaryGeneration === primaryLoadGeneration) &&
          <ReplayEvidencePanels session={replaySession} cutoff={cutoff} symbol={symbol} timeframe={timeframe}
            linkedViews={replayLinked ? chartViewCount : 1}/>}
        {savedSource&&cutoff!=null&&firstFeed&&(!compare||secondFeed)&&
          <FrozenSmtReceipt cutoff={cutoff} timeframe={timeframe} primary={firstFeed} comparison={compare?secondFeed:undefined}/>}
          </section>
          <section id="terminal-watchlist" tabIndex={-1}>      <aside className="market-watchlist"><div className="watch-head"><div><span className="eyebrow">INSTRUMENTS</span><h3>Watchlist</h3></div><ListFilter size={17}/></div><div className="watch-columns"><span>PAIR</span><span>{replayActive?'CLOSE':isBinance?'LAST':'BID'}</span></div>{isBinance&&<div className='watch-row current'><strong>BTCUSDT</strong><span>{last?.close.toFixed(2)??'—'}</span></div>}{(isBinance?[]:watchSymbols).map(w=><button key={w} className={'watch-row '+((source==='MT5'?symbol===w||symbol.toUpperCase().includes(w):symbol===w)?'current':'')} onClick={()=>source==='MT5'?setBrokerSymbol(instruments.find(item=>item.name.toUpperCase().includes(w))?.name ?? w):setSymbol(w)}><span className="watch-symbol"><strong>{w}</strong><small>{w==='XAUUSD'?'Spot gold':w.slice(0,3)+' / '+w.slice(3)}</small></span><span className="watch-price">{(replayActive?(w===symbol?visibleCandles.at(-1)?.close:w===compare?comparison.candles?.at(-1)?.close:undefined):quotes[w])?.toFixed(precisionFor(w))??'—'}</span></button>)}
        <div className="watch-detail"><span className="eyebrow">ACTIVE WORKSPACE</span><div><span>Feed</span><strong>{source}</strong></div><div><span>Instrument</span><strong>{symbol}</strong></div><div><span>Session clock</span><strong>{zone}</strong></div><p>{source==='DEMO'?'All displayed demo quotes are synthetic and are not suitable for market decisions.':'Broker/data availability is not guaranteed. Confirm quote freshness before use.'}</p></div>
        <div className="watch-shortcuts"><strong>Keyboard</strong>{TERMINAL_SHORTCUT_GUIDE.map(([binding,action])=><span key={binding}>{binding} <em>{action}</em></span>)}</div>
      </aside>
          </section>
          <section id="terminal-provenance" tabIndex={-1}><h2>Provenance</h2>
            <p>Candle evidence · {source}. Executable bid/ask quote coverage is available in Data Library.</p>
        {replayArchive&&<FrozenReplayCoverage panes={archivePanes} start={replaySession?.request?.start} end={replaySession?.request?.end}/>}
        <div className="terminal-inspector" role="region" aria-label="Evidence Inspector">
          <DataTrust key={inspectedPane?.view??'primary'}
            data={inspectedPane?inspectionFeed&&{...inspectionFeed,candles:inspectionCandles}:
              replayData?{...replayData,candles:visibleCandles,coverage:undefined}:data}
            symbol={inspectedPane?.data.symbol??symbol} timeframe={inspectedPane?.data.timeframe??timeframe}
            error={replayActive?undefined:error||pollError}/>
        </div>
          </section>
          <section id="terminal-journal" tabIndex={-1}><h2>Journal</h2>
            {journalVisible?<ChartJournal revision={journalRevision} onReturn={location=>{closeSurface();onJournalReturn?.(location);}}/>:<button onClick={()=>setJournalVisible(true)}>Show chart decisions</button>}
          </section>
        </aside>
      </TerminalSurface>
      <TerminalSurface title="Record decision" active={surface==='decision'} close={closeSurface} inline>
        <p>{decisionSymbol} · {decisionFrame}{openTime!==undefined&&' · Candle '+terminalClock(openTime*1000)+' '+zone}</p>
        {cutoff!=null&&<p>Decision cutoff · {terminalClock(cutoff*1000)} {zone}</p>}
        {!chartCapture&&<button disabled={!canSaveCapture||!visibleCandles.length||currentCapture?.saving} onClick={()=>captureButton.current?.click()}>Attach chart capture</button>}
        {surface==='decision'&&currentCapture?.status&&<p role="status">{currentCapture.status}</p>}
        {onPlan&&watchSymbols.includes(decisionSymbol as SymbolName)&&<div className="research-controls">
          <button className="small-tool" disabled={!primaryDecision||!visibleCandles.length||!replayActive&&(busy||Boolean(error))} onClick={()=>{
            if(!primaryDecision)return;
            closeSurface();
            setPlanPrices([]);setInteraction(picking?{mode:'navigate'}:{mode:'plan',prices:[]});
          }}>{picking?'Cancel level selection':'Select plan levels'}</button>
          {!primaryDecision&&<small id="manual-plan-levels">Enter {decisionSymbol} levels in the trade plan. Chart picking is available on the primary pane.</small>}
          {primaryDecision&&picking&&<span role="status">Click primary price for {['entry','stop','target'][picked.length]}</span>}
          <button className="small-tool" aria-describedby={primaryDecision?undefined:'manual-plan-levels'} disabled={replayArchive&&!chartCapture||!replayActive&&(busy||Boolean(error))} onClick={()=>{closeSurface();onPlan({
            symbol:decisionSymbol as SymbolName,timeframe:decisionFrame,snapshot_id:chartCapture,knowledge_cutoff_utc:cutoff==null?undefined:new Date(cutoff*1000).toISOString(),context:`${decisionFeed?.source??source} · ${decisionFeed?.feed_id??'unverified feed'} · ${decisionFeed?.price_basis??'unverified basis'}`,
            entry:inspectedPane&&inspectedPane.view!=='primary'?'':picked[0]??'',stop:inspectedPane&&inspectedPane.view!=='primary'?'':picked[1]??'',target:inspectedPane&&inspectedPane.view!=='primary'?'':picked[2]??'',
          });}}>Create trade plan</button>
          {replayArchive&&compare&&!comparison.candles?.length&&<p role="status">{compare} · {comparison.error?
            'Comparison source unavailable · '+comparison.error:'No comparison candles available at this cutoff'}</p>}
          {replayArchive&&!chartCapture&&<small>Save chart evidence to attach this candle to a decision.</small>}
          {chartCapture&&onChartLesson&&<button className="small-tool" onClick={()=>onChartLesson(chartCapture)}>Record chart lesson</button>}
        </div>}
        {chartCapture&&<ChartPassEditor key={chartCapture} snapshot={chartCapture} symbol={decisionSymbol}
          active={surface==='decision'} onCommitted={()=>setJournalRevision(value=>value+1)}/>}
      </TerminalSurface>
    </div></WorkspacePanels>
  </div>;
}

function Dashboard({overview,loading,error,onRetry,go}:{overview:Overview|null;loading:boolean;error:string;onRetry:()=>void;go:(s:Section)=>void}){
  const formatDate=useTimestampFormat();
  const a=overview?.analytics;return <>
    <PageHeading eyebrow="WORKSPACE" title="Overview" subtitle="Market data, trade plans, and research activity." action={<button className="btn btn-primary" onClick={()=>go('plans')}><Plus size={17}/> Create Trade Plan</button>}/>
    {loading&&!overview?<div className="metrics-grid" aria-busy="true" aria-label="Loading workspace overview">{[0,1,2,3].map(item=><div key={item} className="metric card"><div className="skeleton-grid"><div className="skeleton-line" style={{width:'55%'}}/><div className="skeleton-line" style={{width:'40%',height:28}}/><div className="skeleton-line" style={{width:'75%'}}/></div></div>)}</div>:
    <div className="metrics-grid"><Metric title="Total trade plans" value={String(a?.trades??'—')} change="Immutable version history" icon={<FileText size={18}/>}/><Metric title="Active plans" value={String(a?.active_plans??'—')} change="Awaiting decision or review" icon={<Target size={18}/>}/><Metric title="Journal outcomes" value={String(a?.closed??'—')} change="Manually recorded outcomes" icon={<BarChart3 size={18}/>}/><Metric title="Historical expectancy" value={a?.expectancy_r==null?'—':`${a.expectancy_r>0?'+':''}${a.expectancy_r} R`} change={a?.closed?`${a.closed} recorded outcomes`:'Requires actual closed trades'} icon={<TrendingUp size={18}/>}/></div>}
    {error&&!overview&&<Notice tone="red">Overview metrics unavailable until the local API responds. <button type="button" className="text-button" onClick={onRetry}>Retry connection</button></Notice>}
    <div className="dashboard-main"><div className="main-chart-column"><SectionTitle eyebrow="PRICE ACTION" title="Market overview" action={<button className="text-button" onClick={()=>go('terminal')}>Full terminal <ArrowUpRight size={15}/></button>}/><Terminal/></div><div className="right-column"><SectionTitle eyebrow="ENVIRONMENT" title="System readiness"/><Card className="readiness-card"><div className="readiness-item"><span className="status-icon"><Radio size={17}/></span><div><strong>MT5 terminal</strong><small>{overview?.mt5.connected?'Connected to broker':'Connection required'}</small></div><Badge tone={overview?.mt5.connected?'green':'amber'}>{overview?.mt5.connected?'ONLINE':'OFFLINE'}</Badge></div><div className="readiness-item"><span className="status-icon"><Database size={17}/></span><div><strong>Verified datasets</strong><small>Imported market data</small></div><strong className="mono">{overview?.datasets.length??0}</strong></div><div className="readiness-item"><span className="status-icon"><ShieldCheck size={17}/></span><div><strong>Order execution</strong><small>Read-only environment</small></div><Badge tone="green">LOCKED</Badge></div><div className="readiness-item"><span className="status-icon"><BrainCircuit size={17}/></span><div><strong>AI integration</strong><small>Offline export / import</small></div><Badge tone="blue">MANUAL</Badge></div></Card>
      <Card className="quick-card"><h3>Trade planning</h3><p>Document a setup and calculate its risk.</p><button className="btn btn-secondary full" onClick={()=>go('plans')}>Launch Trade Planner <ArrowRight size={16}/></button></Card></div></div>
    <div className="bottom-dashboard"><Card><SectionTitle eyebrow="WORKFLOW" title="Recent trade plans" action={<button className="text-button" onClick={()=>go('plans')}>View all <ArrowRight size={15}/></button>}/>{loading&&!overview?<div className="loading-zone" role="status"><Spinner/><span>Loading recent trade plans…</span></div>:overview?.recent_trades.length?<div className="table-scroll"><table><thead><tr><th>ID</th><th>PAIR</th><th>BIAS</th><th>STATUS</th><th>CREATED</th></tr></thead><tbody>{overview.recent_trades.map(t=><tr key={t.id}><td className="mono">{t.id}</td><td><strong>{t.symbol}</strong></td><td><Badge tone={t.direction==='BUY'?'green':'red'}>{t.direction}</Badge></td><td>{t.status}</td><td>{formatDate(t.created_at)}</td></tr>)}</tbody></table></div>:<Empty icon={<FileText/>} title="No plans recorded yet" body="Create your first trading hypothesis to populate this timeline."/>}</Card>
    <Card><SectionTitle eyebrow="DISCIPLINE" title="Learning checkpoint"/><div className="checkpoint"><div className="check-progress"><div className="check-progress-inner">{overview?.journal_count??0}</div></div><div><strong>Master journal entries</strong><p>Each question, review, and corrected mistake becomes part of your cumulative history.</p></div></div><button className="btn btn-outline full" onClick={()=>go('journal')}>Open Learning Journal <ArrowRight size={16}/></button></Card></div>
    <div className="demo-strip"><ShieldAlert size={17}/><span>Research-only workspace. Demo candles are synthetic; no profitability or live execution is implied.</span></div>
  </>
}

function Planner({refresh,incoming,onConsume,onReview,initialLocation,onLesson,onPassSaved,openSignal}:{onPassSaved:()=>void;openSignal:(reference:import('./types').SignalReference)=>Promise<void>;refresh:()=>void;incoming?:Plan;onConsume:()=>void;onReview:(id:string)=>void;initialLocation?:{id:string;version?:number};onLesson:(reference:import("./types").PlanReference,snapshot?:string)=>void}){
  const formatDate=useTimestampFormat();
 const [list,setList]=useState<Trade[]>([]);const [loading,setLoading]=useState(true);const [formOpen,setFormOpen]=useState(()=>Boolean(incoming||loadDraft()));const [plan,setPlan]=useState<Plan>(()=>incoming??loadDraft()??{...initialPlan});const [risk,setRisk]=useState<import('./types').Risk|null>(null);const [busy,setBusy]=useState(false);const [message,setMessage]=useState('');const [error,setError]=useState('');const [closeId,setCloseId]=useState('');const [outcome,setOutcome]=useState({pnl:'0',r:'0',notes:''});
 useEffect(()=>{
   if(incoming)onConsume();
 },[incoming,onConsume]);
 useEffect(()=>{
   try {
     if(JSON.stringify(plan)!==JSON.stringify(initialPlan))localStorage.setItem(draftKey,JSON.stringify(plan));
   } catch{/* The draft remains in memory. */}
 },[plan]);
 const [history,setHistory]=useState<Trade>();
 const [historyVersion,setHistoryVersion]=useState(initialLocation?.version);
 useEffect(()=>{
   if(!initialLocation)return;
   let active=true;
   api.trade(initialLocation.id).then(trade=>{if(active)setHistory(trade);})
     .catch(error=>{if(active)setError(error.message);});
   return ()=>{active=false;};
 },[initialLocation]);
 const [declineId,setDeclineId]=useState('');
 const [reason,setReason]=useState('');
 const [guided,setGuided]=useState(false);
 const [step,setStep]=useState(0);
 const [revision,setRevision]=useState<{id:string;version:number}>();
 useEffect(()=>{
   if(incoming)return;
   try{
     const stored=JSON.parse(localStorage.getItem(revisionKey)??'null');
     if(typeof stored?.id==='string'&&Number.isInteger(stored?.version)&&loadDraft())setRevision(stored);
   }catch{}
 },[]);
 function targetRevision(value?:{id:string;version:number}){
   setRevision(value);
   try{if(value)localStorage.setItem(revisionKey,JSON.stringify(value));else localStorage.removeItem(revisionKey);}catch{}
 }
 function editLatest(){
   const version=history?.versions?.[0];
   if(!history||!version||(loadDraft()&&!window.confirm('Replace the unsaved draft with this revision?')))return;
   setPlan(version.plan);targetRevision({id:history.id,version:version.version});
   setRisk(null);setFormOpen(true);setStep(0);setError('');
 }
 async function decline(){
   setBusy(true);setError('');
   try{await api.declinePlan(declineId,reason);setMessage('Recorded no-trade decision '+declineId);
     setDeclineId('');setReason('');await load();refresh();}
   catch(failure){setError((failure as Error).message);}finally{setBusy(false);}
 }
 const [savedContext,setSavedContext]=useState<{reference:import('./types').PlanReference;snapshot:string}>();
 const [savedId,setSavedId]=useState("");
 const load=useCallback(()=>api.trades().then(setList).catch(e=>setError(e.message)).finally(()=>setLoading(false)),[]);useEffect(()=>{void load()},[load]);
 const modify=<K extends keyof Plan>(key:K,value:Plan[K])=>{setPlan(p=>({...p,[key]:value}));setRisk(null)};
 async function calculate(){setBusy(true);setError('');try{setRisk(await api.risk(plan))}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function save(){setBusy(true);setError('');try{const result=revision?await api.revisePlan(revision.id,revision.version,plan):await api.createPlan(plan);setMessage((revision?'Saved revision for ':'Created ')+result.id);setSavedContext(plan.snapshot_id?{reference:{trade_id:result.id,version:result.version??(revision?revision.version+1:1)},snapshot:plan.snapshot_id}:undefined);targetRevision();setSavedId(result.id);setFormOpen(false);setRisk(null);setPlan({...initialPlan});try{localStorage.removeItem(draftKey)}catch{}await load();refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function finalize(){if(!closeId)return;setBusy(true);setError('');try{await api.closeTrade(closeId,outcome.pnl,outcome.r,outcome.notes);setCloseId('');await load();refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <><PageHeading eyebrow="TRADING / PLANNING" title="Trade planner" subtitle="A trade should exist as a written hypothesis before it exists as a position." action={<button className="btn btn-primary" onClick={()=>{setFormOpen(true);setMessage('');setError('')}}><Plus size={17}/> New Trade Plan</button>}/>
  <div className="pill-guidance"><ShieldCheck size={17}/><div><strong>Evidence-first workflow</strong><span>Context → Observation → Evidence → Hypothesis → Setup → Trigger → Invalidation → Risk → Execution → Review</span></div></div>
  {message&&<Success>{message}</Success>}{savedContext&&<button className="small-tool" onClick={()=>onLesson(savedContext.reference,savedContext.snapshot)}>Journal this plan revision</button>}{savedId&&<button className="small-tool" onClick={()=>onReview(savedId)}>Review saved plan</button>}{error&&<Notice tone="red">{error}</Notice>}
  <Card><SectionTitle eyebrow="PLANNED DECISIONS" title="Your trade log" action={<Badge tone="neutral">{list.length} records</Badge>}/>{loading?<div className="loading-zone"><Spinner/></div>:list.length?<div className="table-scroll"><table><thead><tr><th>DECISION</th><th>SYMBOL</th><th>STRATEGY</th><th>RISK</th><th>STATUS</th><th>CREATED</th><th>ACTION</th></tr></thead><tbody>{list.map(t=><tr key={t.id}><td className="mono">{t.id}</td><td><strong>{t.symbol}</strong> <span className={t.direction==='BUY'?'positive':'negative'}>{t.direction}</span></td><td>{t.plan.strategy}</td><td>{t.plan.risk_percent}%</td><td><Badge tone={t.status==='CLOSED'?'blue':'green'}>{t.status}</Badge></td><td>{formatDate(t.created_at)}</td><td><button className="text-button" onClick={()=>{setHistoryVersion(undefined);setHistory(undefined);void api.trade(t.id).then(setHistory).catch(reason=>setError(reason.message));}}>Versions</button>
  {t.status==='PLANNED'?<><button className="text-button" onClick={()=>setCloseId(t.id)}>Record outcome <ArrowRight size={14}/></button>
    <button className="text-button" onClick={()=>setDeclineId(t.id)}>No trade</button></>:t.outcome?`${t.outcome.result_r} R`:'—'}</td></tr>)}</tbody></table></div>:<Empty icon={<Target/>} title="Your trade log is empty" body="Start with a structured plan. Your decisions will be versioned and preserved here."/>}</Card>
  {history&&<PlanHistory key={history.id+(history.versions?.[0]?.version??0)} trade={history} onSignal={ref=>void openSignal(ref).catch(e=>setError(e.message))} initialVersion={historyVersion} onRevise={editLatest} onLesson={version=>onLesson({trade_id:history.id,version},history.versions?.find(item=>item.version===version)?.plan.snapshot_id)} onClose={()=>setHistory(undefined)}/>}
  {declineId&&<div className="modal-shade"><section className="small-modal" role="dialog" aria-label="Record no-trade decision">
    <h2>Record no-trade decision</h2><TextArea label="Reason for passing" value={reason} onChange={setReason}/>
    <div className="research-controls"><button className="small-tool" onClick={()=>setDeclineId('')}>Cancel</button>
      <button className="btn btn-primary" disabled={busy||reason.trim().length<5} onClick={()=>void decline()}>Save no-trade decision</button></div>
    {error&&<Notice tone="red">{error}</Notice>}
  </section></div>}
  {closeId&&<div className="modal-shade" onMouseDown={()=>setCloseId('')}><div className="small-modal" role="dialog" aria-label="Record outcome" onMouseDown={e=>e.stopPropagation()}><div className="modal-top"><h2>Record outcome</h2><button className="icon-btn" aria-label="Close record outcome" onClick={()=>setCloseId('')}><X/></button></div><p className="muted">Manual journal result; this is not broker-reconciled execution accounting.</p><TextField label="Realized P&L (USD)" value={outcome.pnl} onChange={v=>setOutcome(s=>({...s,pnl:v}))}/><TextField label="Result in R" value={outcome.r} onChange={v=>setOutcome(s=>({...s,r:v}))}/><TextArea label="Review notes" value={outcome.notes} onChange={v=>setOutcome(s=>({...s,notes:v}))}/><button className="btn btn-primary full" disabled={busy} onClick={()=>void finalize()}>Save immutable outcome</button></div></div>}
  {formOpen&&<div className="drawer-overlay" onMouseDown={()=>setFormOpen(false)}><div className="drawer" role="dialog" aria-label="Trade plan builder" onMouseDown={e=>e.stopPropagation()}><div className="drawer-heading"><div><span className="eyebrow">PLAN BUILDER / V1</span><h2>{revision?`Revise ${revision.id} · from v${revision.version}`:'New trade hypothesis'}</h2><p>Complete each evidence field to create an auditable plan.</p></div><button className="icon-btn" aria-label="Close trade plan builder" onClick={()=>setFormOpen(false)}><X size={20}/></button></div><div className="drawer-body">
    <div className="research-controls"><label><input type="checkbox" checked={guided}
      onChange={event=>setGuided(event.target.checked)}/> Guided planner</label>
      {guided&&['Context and evidence','Levels and risk'].map((label,index)=><button className="small-tool" key={label}
        aria-pressed={step===index} onClick={()=>setStep(index)}>{label}</button>)}</div>
    <section hidden={guided&&step!==0}>
    <div className="form-subtitle">Market parameters</div><div className="form-grid three"><label className="field"><span>Instrument</span><Select value={plan.symbol} onChange={v=>modify('symbol',v as SymbolName)} options={['EURUSD','GBPUSD','USDJPY','XAUUSD']}/></label><label className="field"><span>Direction</span><Select value={plan.direction} onChange={v=>modify('direction',v as 'BUY'|'SELL')} options={['BUY','SELL']}/></label><label className="field"><span>Timeframe</span><Select value={plan.timeframe} onChange={v=>modify('timeframe',v as Timeframe)} options={['M1','M3','M5','M15','M30','H1','H4','D1']}/></label></div>
    <div className="form-grid"><TextField label="Strategy / model" value={plan.strategy} onChange={v=>modify('strategy',v)}/><label className="field"><span>Session</span><Select value={plan.session} onChange={v=>modify('session',v as Plan['session'])} options={['ASIA','LONDON','NEW_YORK','OTHER']}/></label></div>
    {plan.snapshot_id&&<p><a href={`/research/api/chart-snapshots/${plan.snapshot_id}/image`} target="_blank" rel="noreferrer">Open linked chart capture</a>
      <button className="text-button" onClick={()=>modify('snapshot_id',undefined)}>Remove chart capture</button></p>}
    {plan.snapshot_id&&!revision&&<details><summary>Pass on this setup</summary>
      <TextArea label="Reason to pass" value={reason} onChange={setReason}/>
      <button className="small-tool" disabled={busy||reason.trim().length<5} onClick={async()=>{
        setBusy(true);setError('');
        try{
          await api.addJournal('CHART','No trade · '+plan.symbol,reason.trim(),plan.signal_reference,
            {snapshot_id:plan.snapshot_id,tags:['no-trade']});
          localStorage.removeItem(draftKey);onPassSaved();
        }catch(e){setError((e as Error).message);}finally{setBusy(false);}
      }}>Save pass to journal</button>{error&&<p role="alert">{error}</p>}
    </details>}
    {plan.snapshot_id&&<SnapshotDetails id={plan.snapshot_id}/>}
    {plan.signal_reference&&<p role="status">Linked SMT revision · {plan.signal_reference.status} · {plan.signal_reference.event_key.slice(0,12)}</p>}
    <div className="form-subtitle">Evidence chain <button className="text-button" onClick={()=>setPlan(p=>({...p,...snippets}))}>Fill example wording (edit before saving)</button></div>
    {(['context','observation','evidence','hypothesis','setup','trigger','invalidation'] as const).map((key,index)=><TextArea key={key} label={`${String(index+1).padStart(2,'0')} · ${key.charAt(0).toUpperCase()+key.slice(1)}`} value={plan[key]} onChange={v=>modify(key,v)} placeholder={`Enter ${key} with observed levels, times, or rules` } rows={2}/>)}
    <TextArea label="Management plan" value={plan.management} onChange={v=>modify('management',v)} rows={2}/>
    </section>
    <section hidden={guided&&step!==1}>
    <div className="form-subtitle">Levels and risk</div><div className="form-grid three"><TextField label="Entry" value={plan.entry} onChange={v=>modify('entry',v)}/><TextField label="Stop" value={plan.stop} onChange={v=>modify('stop',v)}/><TextField label="Target" value={plan.target} onChange={v=>modify('target',v)}/></div>
    <div className="form-grid three"><TextField label="Equity USD" value={plan.equity_usd} onChange={v=>modify('equity_usd',v)}/><TextField label="Risk % (max 2)" value={plan.risk_percent} onChange={v=>modify('risk_percent',v)}/><TextField label="Pip value / lot USD" value={plan.pip_value_per_lot_usd} onChange={v=>modify('pip_value_per_lot_usd',v)}/></div>
    <Notice>The pip value and proposed lot step are illustrative; verify broker contract specifications before any real order.</Notice>
    {risk&&<div className="risk-preview"><div><span>Planned maximum loss</span><strong>${risk.maximum_planned_loss_usd}</strong></div><div><span>Illustrative size</span><strong>{risk.illustrative_lots} lots</strong></div><div><span>Reward/risk</span><strong>1 : {risk.reward_risk}</strong></div></div>}
    </section>
    {error&&<Notice tone="red">{error}</Notice>}
  </div><div className="drawer-footer"><button className="btn btn-outline" disabled={busy} onClick={()=>void calculate()}><ShieldCheck size={16}/> Calculate Risk</button><button className="btn btn-primary" disabled={busy} onClick={()=>void save()}><FileCheck2 size={16}/> {revision?'Save revision':'Save plan'} <ArrowRight size={16}/></button></div></div></div>}
 </>
}

function AIPage({refresh,initialTrade}:{refresh:()=>void;initialTrade:string}){
  const formatDate=useTimestampFormat();
 const [trades,setTrades]=useState<Trade[]>([]);const [packages,setPackages]=useState<Package[]>([]);const [reviews,setReviews]=useState<Review[]>([]);const [chosen,setChosen]=useState(initialTrade);const [input,setInput]=useState('');const [busy,setBusy]=useState(false);const [result,setResult]=useState('');const [error,setError]=useState('');
 const load=useCallback(()=>Promise.all([api.trades(),api.packages(),api.reviews()]).then(([t,p,r])=>{setTrades(t);setPackages(p);setReviews(r);if(!chosen&&t.length)setChosen(t[0].id)}).catch(e=>setError(e.message)),[chosen]);useEffect(()=>{void load()},[load]);
 async function exportPackage(){if(!chosen)return;setBusy(true);setError('');try{const item=await api.createExport(chosen,trades.find(trade=>trade.id===chosen)?.version);setResult(`Package ${item.package_id} created. Ready for download.`);window.open(item.download_url,'_blank');await load();refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function importResponse(){setBusy(true);setError('');try{JSON.parse(input);const review=await api.importReview(input);setResult(`Imported ${review.id} — ${review.verification}`);setInput('');await load();refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 const example=(p=packages[0],corrects?:string)=>{if(!p)return;setInput(JSON.stringify({schema_version:1,...(corrects?{corrects_review_id:corrects}:{}),package_id:p.id,trade_decision_id:p.trade_id,plan_version:p.plan_version,review_type:'PRE_TRADE',assessment:'INSUFFICIENT_DATA',findings:[{summary:'Sample observation only: verify spread and completed session structure.',evidence_refs:['PLAN','RISK'],metric_refs:[]}],evidence_refs:['PLAN','RISK'],missing_evidence:['Historical analog records'],contradictions:[],risk_concerns:[],suggested_actions:[],limitations:['Free-text claims require manual verification.']},null,2))};
 return <><PageHeading eyebrow="INTELLIGENCE / FRONTIER AI" title="AI Review Center" subtitle="Export complete evidence. Challenge your thesis in your preferred frontier chat AI. Bring the review back." action={<Badge tone="blue"><CloudOff size={13}/> NO API REQUIRED</Badge>}/>
  <div className="ai-feature"><div className="ai-feature-icon"><BrainCircuit size={28}/></div><div><span className="eyebrow">HUMAN-IN-THE-LOOP INTELLIGENCE</span><h2>Evidence goes out. Critical thinking comes back.</h2><p>Versioned Markdown and JSON packages preserve your plan, rationale, risk calculations and review cutoff.</p></div><div className="ai-feature-path"><span>01 EXPORT</span><ArrowRight size={15}/><span>02 REVIEW</span><ArrowRight size={15}/><span>03 IMPORT</span></div></div>
  {error&&<Notice tone="red">{error}</Notice>}{result&&<Success>{result}</Success>}
  <div className="two-column"><Card><SectionTitle eyebrow="STEP 01" title="Generate review package"/><p className="muted">Choose a plan. The export is frozen at its current version and cutoff.</p><label className="field"><span>Trade decision</span><div className="select-wrap"><select value={chosen} onChange={e=>setChosen(e.target.value)}><option value="">Select trade plan</option>{trades.map(t=><option key={t.id} value={t.id}>{t.id} · {t.symbol} {t.direction} · v{t.version}</option>)}</select></div></label><div className="export-checks"><div><CheckCircle2 size={17}/> Immutable plan snapshot</div><div><CheckCircle2 size={17}/> Local risk calculation</div><div><CheckCircle2 size={17}/> Cutoff-filtered event timeline</div><div><CheckCircle2 size={17}/> Versioned response template</div></div><button className="btn btn-primary full" disabled={!chosen||busy} onClick={()=>void exportPackage()}><Download size={17}/> Generate & Download ZIP</button><Notice tone="blue">Current exports include local plans and risk evidence. Historical analogs are not invented or automatically attached.</Notice></Card>
  <Card><SectionTitle eyebrow="STEP 02 + 03" title="Import frontier AI review"/><p className="muted">Upload the package manually to a chat AI. Paste its structured JSON response here.</p><label className="field"><span>AI response (JSON)</span><textarea className="json-area" value={input} onChange={e=>setInput(e.target.value)} placeholder={'{\n  "package_id": "PKG-...",\n  "assessment": "INSUFFICIENT_DATA",\n  "findings": []\n}'} rows={10}/></label><div className="split-buttons"><button className="btn btn-outline" disabled={!packages.length} onClick={()=>example()}>Load example template</button><button className="btn btn-primary" disabled={!input.trim()||busy} onClick={()=>void importResponse()}><Upload size={16}/> Validate & Import</button></div><small className="muted">The importer checks IDs, schema and evidence references, but cannot independently verify free-text claims.</small></Card></div>
  <Card><SectionTitle eyebrow="HISTORY" title="Review packages"/><div className="table-scroll"><table><thead><tr><th>PACKAGE</th><th>TRADE DECISION</th><th>PLAN</th><th>CREATED</th><th>ACTION</th></tr></thead><tbody>{packages.map(p=><tr key={p.id}><td className="mono">{p.id}</td><td className="mono">{p.trade_id}</td><td>v{p.plan_version}</td><td>{formatDate(p.created_at)}</td><td><a className="text-button" href={`/research/api/ai/packages/${p.id}/download`} target="_blank" rel="noreferrer"><Download size={14}/> Download</a></td></tr>)}</tbody></table>{!packages.length&&<Empty icon={<FolderOpen/>} title="No exported packages" body="Export your first trade plan to begin a versioned AI review thread."/>}</div></Card>
  {reviews.length>0&&<Card><SectionTitle eyebrow="REVIEW RESULTS" title="Imported assessments"/><div className="review-list">{reviews.map(r=><div className="review-item" key={r.id}><div><strong>{r.assessment}</strong><small>{r.id} · {formatDate(r.created_at)}</small></div><Badge tone="amber">CONTENT UNVERIFIED</Badge>{r.original_integrity==='DUPLICATE_JSON_KEY'&&<><strong>Ambiguous original · duplicate JSON key {r.duplicate_key}</strong><button className="small-tool" onClick={async()=>{if(r.recorded_package){try{const p=await api.get<{template:object}>('/ai/packages/'+r.package_id+'/recorded');setInput(JSON.stringify({...p.template,corrects_review_id:r.id},null,2))}catch(e){setError(String(e))}}else{const p=packages.find(p=>p.id===r.package_id);if(p)example(p,r.id)}}}>Prepare linked correction</button></>}{r.corrects_review_id&&<small>Corrects original {r.corrects_review_id}</small>}{!!r.recorded_package&&<a className="text-button" href={`/research/api/ai/packages/${r.package_id}/recorded`} target="_blank" rel="noreferrer">Frozen replay evidence</a>}{r.has_original&&<a className="text-button" href={`/research/api/ai/reviews/${encodeURIComponent(r.id)}/original`}><Download size={14}/> Original JSON</a>}</div>)}</div></Card>}
 </>
}

function StrategyLab(){
  const formatDate=useTimestampFormat();
  const [range,setRange]=useState<CandleRange>();
 const [symbol,setSymbol]=useState<SymbolName>('EURUSD');const [timeframe,setTimeframe]=useState<Timeframe>('M5');const [source,setSource]=useState<SourceName>('MT5');const [result,setResult]=useState<BacktestResult|null>(null);const [error,setError]=useState('');const [loading,setLoading]=useState(false);
 const [savedRun,setSavedRun]=useState<BacktestRun>();
 const [runs,setRuns]=useState<BacktestRunSummary[]>([]);
 const [compareRun,setCompareRun]=useState('');
 const [archiveError,setArchiveError]=useState('');
 const refreshRuns=useCallback(()=>api.backtestRuns().then(value=>{setRuns(value);setArchiveError('');})
   .catch(error=>setArchiveError(error.message)),[]);
 useEffect(()=>{void refreshRuns();},[refreshRuns]);
 async function openRun(id:string){
   if(!id)return;
   setLoading(true);setError('');
   try{const snapshot=await api.backtestRun(id);setSavedRun(snapshot);setResult(snapshot.result);}
   catch(reason){setError((reason as Error).message);}finally{setLoading(false);}
 }
 const [tickArchives,setTickArchives]=useState<Dataset[]>([]);
 const [tickId,setTickId]=useState("");
 const [slippage,setSlippage]=useState("0");
 const [commission,setCommission]=useState("0");
 const [maxGap,setMaxGap]=useState("60");
 const saved=source.endsWith("_SAVED");
 useEffect(()=>{
   if(!saved)return;
   let active=true;
   api.datasets().then(data=>{if(active)setTickArchives(data)}).catch(error=>{if(active)setError(error.message)});
   return()=>{active=false};
 },[saved]);
 const choices=tickArchives.filter(dataset=>dataset.kind==="ticks"&&dataset.symbol===symbol&&dataset.provider+"_SAVED"===source);
 const tick=saved&&(tickId==="archive-range"||choices.some(dataset=>dataset.id===tickId))?{id:tickId,slippage,commission,maxGap}:undefined;
 const validGap=Number.isInteger(Number(maxGap))&&Number(maxGap)>=1&&Number(maxGap)<=60;
 const validCosts=[slippage,commission].every(value=>value.trim()!==""&&Number(value)>=0&&Number(value)<=100);
 async function run(){
   setLoading(true);setError('');setResult(null);setSavedRun(undefined);
   try{
     const snapshot=await api.saveBacktest(symbol,timeframe,source,saved?range:undefined,tick);
     setSavedRun(snapshot);setResult(snapshot.result);void refreshRuns();
   }catch(e){setError((e as Error).message)}finally{setLoading(false);}
 }
 const comparisonRun=runs.find(item=>item.id===compareRun);
 const currentRun=runs.find(item=>item.id===savedRun?.id);
 return <><PageHeading eyebrow="QUANT RESEARCH / LAB" title="Strategy laboratory" subtitle="Reproducible testing without look-ahead or misleading certainty."/><Notice tone="amber">This laboratory tests one breakout rule. Bid/ask replay supports slippage and commission. OHLC simulation excludes execution costs.</Notice><div className="two-column lab-layout"><Card><SectionTitle eyebrow="SIMULATION SETUP" title="Research parameters"/><label className="field"><span>Instrument</span><Select value={symbol} onChange={v=>setSymbol(v as SymbolName)} options={['EURUSD','GBPUSD','USDJPY','XAUUSD']}/></label><label className="field"><span>Timeframe</span><Select value={timeframe} onChange={v=>setTimeframe(v as Timeframe)} options={['M1','M3','M5','M15','M30','H1','H4','D1']}/></label><label className="field"><span>Data source</span><Select value={source} onChange={v=>setSource(v as SourceName)} options={['DEMO','MT5','MT5_SAVED','DUKASCOPY_SAVED']}/></label>{source.endsWith('_SAVED')&&<SavedRange range={range} onChange={setRange}/>}{saved&&<label className="field"><span>Execution data</span><div className="select-wrap"><select value={tick?.id??""} onChange={event=>setTickId(event.target.value)}>
    <option value="">OHLC candles</option><option value="archive-range">Bid/ask ticks across saved range</option>{choices.map(dataset=><option key={dataset.id} value={dataset.id}>Bid/ask ticks · {dataset.rows_count.toLocaleString()} · {dataset.id.slice(0,8)}</option>)}
  </select></div></label>}
  {tick&&<div className="two-column"><label className="field"><span>Slippage per side (bps)</span><input type="number" min="0" max="100" step="any" value={slippage} onChange={event=>setSlippage(event.target.value)}/></label><label className="field"><span>Commission per side (bps)</span><input type="number" min="0" max="100" step="any" value={commission} onChange={event=>setCommission(event.target.value)}/></label><label className="field"><span>Maximum quote gap (seconds)</span><input type="number" min="1" max="60" step="1" value={maxGap} onChange={event=>setMaxGap(event.target.value)}/></label></div>}
  <div className="lab-model"><strong>Mechanical baseline</strong><p>Previous-bar breakout → enter at next bar open → 2R target → stop at recent extreme → max 12 bars. Ambiguous exits contribute to expectancy bounds.</p></div><button className="btn btn-primary full" disabled={loading||Boolean(tick&&(!validCosts||!validGap))} onClick={()=>void run()}>{loading?<Spinner/>:<Zap size={17}/>} Run historical simulation</button></Card>
 <Card className="lab-results"><SectionTitle eyebrow="RESEARCH OUTPUT" title="Simulation summary"/>{result?<><div className="lab-metrics"><div><span>Eligible signals</span><strong>{result.eligible_signals}</strong></div><div><span>Win rate</span><strong>{result.win_rate==null?'—':result.win_rate+'%'}</strong></div><div><span>Expectancy</span><strong className={result.expectancy_r!=null&&result.expectancy_r>=0?'positive':'negative'}>{result.expectancy_r==null?'—':`${result.expectancy_r} R`}</strong></div><div><span>Ambiguous exits</span><strong>{result.ambiguous_excluded}{result.ambiguous_rate==null?'':` (${result.ambiguous_rate}%)`}</strong></div></div><Badge tone={result.synthetic?'amber':'blue'}>{result.synthetic?'SYNTHETIC · NOT EVIDENCE':'BROKER/DATASET SOURCE'}</Badge><p className="muted small">{result.data_note}</p>{result.unresolved_outcomes?.map(outcome=><Notice key={outcome.signal_time}>Replay stopped at {new Date(outcome.signal_time*1000).toISOString()}. {outcome.reason.toLowerCase().replaceAll('_',' ')}.</Notice>)}{Boolean(result.candle_gaps?.length)&&<Notice>The selected range has candle gaps. Only verified individual trades are shown.</Notice>}{result.ambiguous_excluded>0&&result.expectancy_bounds_r&&<Notice>Expectancy is indeterminate from OHLC. Bounds: {result.expectancy_bounds_r.worst} to {result.expectancy_bounds_r.best} R per signal. Tick replay is required.</Notice>}</>:<Empty icon={<FlaskConical/>} title="No simulation results" body="Choose a dataset and run the mechanical baseline to inspect its limitations."/>}</Card></div>
 {error&&<Notice tone="red">{error}</Notice>}<Card><SectionTitle title="Saved simulation runs"/>
   {archiveError&&<Notice tone="red">{archiveError}</Notice>}
   {runs.length>0?<div className="research-controls">
     <label>Open saved run<select value={savedRun?.id??''} disabled={loading} onChange={event=>void openRun(event.target.value)}>
       <option value="">Choose a run</option>{runs.map(item=><option key={item.id} value={item.id}>{item.symbol} · {item.timeframe} · {item.model} · {formatDate(item.created_at)}</option>)}
     </select></label>
     {currentRun&&<label>Compare with<select value={compareRun===currentRun.id?'':compareRun} onChange={event=>setCompareRun(event.target.value)}>
       <option value="">Choose another run</option>{runs.filter(item=>item.id!==currentRun.id).map(item=><option key={item.id} value={item.id}>{item.symbol} · {item.model} · {formatDate(item.created_at)}</option>)}
     </select></label>}
   </div>:!archiveError&&<p className="muted">No saved simulations yet.</p>}
   {currentRun&&comparisonRun&&currentRun.id!==comparisonRun.id&&<div className="evidence-pairs">
     {[currentRun,comparisonRun].map(item=><section key={item.id}><h3>{item.symbol} · {item.timeframe} · {item.model}</h3>
       <p>{item.source} · {item.count} recorded trades</p><p>Expectancy {item.expectancy_r===null?'Unavailable':`${item.expectancy_r} R`}</p>
     </section>)}
   </div>}
 </Card>{result&&<SimulationEvidence key={savedRun?.id} result={result} run={savedRun}/>}{result&&<Card><SectionTitle eyebrow="SIMULATION DETAILS" title="Recorded sample trades"/><div className="table-scroll"><table><thead><tr><th>ENTRY TIME</th><th>DIRECTION</th><th>ENTRY</th><th>STOP</th><th>TARGET</th><th>RESULT</th></tr></thead><tbody>{result.trades.map((t,i)=><tr key={i}><td>{formatDate(t.entry_time*1000)}</td><td>{t.side}</td><td>{t.entry.toFixed(5)}</td><td>{t.stop.toFixed(5)}</td><td>{t.target.toFixed(5)}</td><td className={t.result_r>=0?'positive':'negative'}>{t.result_r>0?'+':''}{t.result_r} R</td></tr>)}</tbody></table>{!result.trades.length&&<p className="muted">No eligible unambiguous simulated trades.</p>}</div></Card>}
 </>
}

function Journal({refresh,incoming,clearLink,openSignal,initialEntry,initialContext,openPlan,onReturn}:{onReturn:(location:DecisionLocation)=>void;
  refresh:()=>void;incoming?:SignalRecord;initialEntry?:JournalEntry;initialContext?:JournalContext;clearLink:()=>void;openPlan:(id:string,version:number)=>void;openSignal:(reference:import('./types').SignalReference)=>Promise<void>;
}){
  const formatDate=useTimestampFormat();
 const [entries,setEntries]=useState<JournalEntry[]>([]);
 const [entryQuery,setEntryQuery]=useState("");
 const [entryKind,setEntryKind]=useState("");
 const [tags,setTags]=useState('');
 const [revisionEntry,setRevisionEntry]=useState<JournalEntry>();
 useEffect(()=>setRevisionEntry(undefined),[initialContext]);
 const [focusEntry,setFocusEntry]=useState(initialEntry);
 useEffect(()=>setFocusEntry(initialEntry),[initialEntry]);const [title,setTitle]=useState('');const [content,setContent]=useState('');const [kind,setKind]=useState(incoming||initialContext?.snapshot_id?'CHART':'T');const [error,setError]=useState('');const [message,setMessage]=useState('');const [busy,setBusy]=useState(false);
 const load=useCallback(()=>api.journal().then(setEntries).catch(e=>setError(e.message)),[]);useEffect(()=>{void load()},[load]);
 async function add(){setBusy(true);setError('');try{const e=await api.addJournal(kind,title,content,revisionEntry?.signal_reference??(incoming?{event_key:incoming.event_key,status:incoming.status}:undefined),{
   snapshot_id:revisionEntry?.snapshot_id??initialContext?.snapshot_id,plan_reference:revisionEntry?.plan_reference??initialContext?.plan_reference,
   supersedes:revisionEntry?.id,tags:tags.split(',').map(tag=>tag.trim()).filter(Boolean),
 });setMessage(`${e.id} appended to Master Journal`);setTitle('');setContent('');setTags('');setRevisionEntry(undefined);clearLink();await load();refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <><PageHeading eyebrow="ACADEMY / REFLECTION" title="Master Learning Journal" subtitle="One cumulative history of questions, chart challenges, mistakes, and corrected reasoning." action={<><a className="btn btn-outline" href="?practice">Review a historical decision</a><a href="/research/api/journal/download" className="btn btn-secondary"><Download size={16}/> Export Markdown</a></>}/>
 <LearningDrill saved={()=>{void load();refresh()}}
   openJournal={async id=>{setFocusEntry(await api.get<JournalEntry>('/journal/entries/'+encodeURIComponent(id)))}}/>
 <div className="two-column journal-layout"><Card><SectionTitle eyebrow="CAPTURE KNOWLEDGE" title={revisionEntry?'Revision of '+revisionEntry.id:'New journal entry'}/>
 {revisionEntry&&<button className="text-button" onClick={()=>{setRevisionEntry(undefined);setTitle('');setContent('');setTags('');}}>Cancel revision</button>}
 {initialContext?.plan_reference&&<p>Linked plan {initialContext.plan_reference.trade_id} · v{initialContext.plan_reference.version}</p>}
 {initialContext?.snapshot_id&&<SnapshotDetails id={initialContext.snapshot_id}/>}
 {initialContext?.snapshot_id&&<p><a href={`/research/api/chart-snapshots/${initialContext.snapshot_id}/image`} target="_blank" rel="noreferrer">Open attached chart capture</a></p>}
 {incoming&&<p>{signalSummary(incoming)} <button className="text-button" onClick={clearLink}>Remove signal link</button></p>}<label className="field"><span>Entry category</span><Select value={kind} onChange={setKind} options={['T','Q','CHART','RM','M','R']}/></label><TextField label="Title" value={title} onChange={setTitle} placeholder="What did you learn?"/><TextField label="Tags (comma separated)" value={tags} onChange={setTags}/><TextArea label="Observation / explanation" value={content} onChange={setContent} placeholder="Record the evidence, uncertainty, and what changed in your thinking." rows={7}/><button className="btn btn-primary full" disabled={busy||title.trim().length<2||content.trim().length<5} onClick={()=>void add()}><Plus size={17}/> Append to journal</button>{message&&<Success>{message}</Success>}{error&&<Notice tone="red">{error}</Notice>}</Card>
 <Card>{focusEntry&&<div className="data-trust">
   <h3>{focusEntry.id} · {focusEntry.title}</h3><p>{focusEntry.content}</p>
   <button className="text-button" onClick={()=>setFocusEntry(undefined)}>Close selected entry</button></div>}
   <div className="research-controls">
     <label>Search journal<input value={entryQuery} onChange={event=>setEntryQuery(event.target.value)}/></label>
     <label>Filter by category<select value={entryKind} onChange={event=>setEntryKind(event.target.value)}>
       <option value="">All</option>{['T','Q','CHART','RM','M','R'].map(value=><option key={value}>{value}</option>)}
     </select></label>
   </div><SectionTitle eyebrow="CUMULATIVE TIMELINE" title="Learning history" action={<Badge tone="neutral">{entries.length} entries</Badge>}/><div className="journal-feed">{entries.filter(item=>(!entryKind||entryKind===item.kind)&&[item.id,item.title,item.content,...(item.tags??[])].some(value=>value.toLowerCase().includes(entryQuery.trim().toLowerCase()))).map(e=><div className="journal-item" key={e.id}><div className="journal-rail"><div className="journal-dot"/></div><div><span className="mono tag-id">{e.id}</span><h4>{e.title}</h4><p>{e.content}</p>
 {e.tags?.length?<small>{e.tags.join(' · ')}</small>:null}
 {e.supersedes&&<small>Supersedes {e.supersedes}</small>}
 {e.plan_reference&&<button className="text-button" onClick={()=>openPlan(e.plan_reference!.trade_id,e.plan_reference!.version)}>
   Open linked plan v{e.plan_reference.version}</button>}
 {e.plan_reference&&<JournalPaper trade={e.plan_reference.trade_id} version={e.plan_reference.version}/>}
 {e.snapshot_id&&<SnapshotDetails id={e.snapshot_id} onReturn={onReturn}/>}
 {e.snapshot_id&&<a className="text-button" href={`/research/api/chart-snapshots/${e.snapshot_id}/image`} target="_blank" rel="noreferrer">Open chart capture</a>}
 {!entries.some(item=>item.supersedes===e.id)&&<button className="text-button" onClick={()=>{
   setRevisionEntry(e);setKind(e.kind);setTitle(e.title);setContent(e.content);setTags(e.tags?.join(', ')??'');
 }}>Revise entry</button>}{e.signal_reference&&<button className="text-button" onClick={()=>void openSignal(e.signal_reference!).catch(reason=>setError(reason.message))}>Open linked signal revision</button>}<small>{formatDate(e.created_at)}</small></div></div>)}{!entries.length&&<Empty icon={<BookOpen/>} title="Build your knowledge base" body="Start a topic (T) or a question (Q); every entry receives a stable ID."/>}</div></Card></div>
 </>
}

function DataLibrary({refresh,initialDataset,openReplay}:{refresh:()=>void;initialDataset?:Dataset;openReplay:(range:CandleRange)=>void}){
  const formatDate=useTimestampFormat();
 const [datasets,setDatasets]=useState<Dataset[]>([]);const [symbol,setSymbol]=useState<SymbolName>('EURUSD');const [timeframe,setTimeframe]=useState<Timeframe>('M5');const [day,setDay]=useState('2026-11-12');const [hour,setHour]=useState('');const [file,setFile]=useState<File|null>(null);const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [msg,setMsg]=useState('');
 const [inspected,setInspected]=useState<Dataset|undefined>(initialDataset);
 useEffect(()=>{if(initialDataset)setInspected(initialDataset);},[initialDataset]);
 const [reviewedFile,setReviewedFile]=useState<File|null>(null);
 async function registerReviewed(){
   if(!reviewedFile)return;
   setBusy(true);setMsg('');setError('');
   try{
     const receipt=await api.importReviewed(reviewedFile);
     setMsg('Verified '+receipt.partitions.length+' partitions. Source SHA-256 '+receipt.source_sha256);
     await load();refresh();
   }catch(reason){setError((reason as Error).message)}finally{setBusy(false)}
 }
 const [trust,setTrust]=useState<CandleResponse>();
 const [trustError,setTrustError]=useState('');
 useEffect(()=>{
   if(!inspected)return;
   let active=true;
   setTrust(undefined);setTrustError('');
   api.get<CandleResponse>('/datasets/'+encodeURIComponent(inspected.id)+'/coverage')
     .then(value=>{if(active)setTrust(value);}).catch(reason=>{if(active)setTrustError(reason.message);});
   return()=>{active=false;};
 },[inspected]);
 const [datasetsLoading,setDatasetsLoading]=useState(true);
 const load=useCallback(()=>api.datasets().then(setDatasets).catch(e=>setError(e.message)).finally(()=>setDatasetsLoading(false)),[]);useEffect(()=>{void load()},[load]);
 async function importMT5(){setBusy(true);setError('');try{const d=await api.importMt5(symbol,timeframe,500) as {rows:number};setMsg(`Imported ${d.rows} verified MT5 bars`);await load();refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function importBi5(){if(!file)return;setBusy(true);setError('');try{const d=await api.importBi5(symbol,day,file,hour);setMsg(`Imported ${d.tick_dataset.rows} source ticks, ${d.candle_rows} M5 bars`);await load();refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <><PageHeading eyebrow="STORAGE / PROVENANCE" title="Market data library" subtitle="Maintain separately sourced, checksummed datasets for research and replay." action={<a className="btn btn-secondary" href="/catalog.html">Open fixed-point catalog</a>}/>
 {error&&<Notice tone="red">{error}</Notice>}{msg&&<Success>{msg}</Success>}
 <ForexCoverage openReplay={openReplay} imported={()=>{void load();refresh();}}/>
 <Card><SectionTitle title="Register a reviewed local archive"/>
 <p>Choose an EURUSD BI5 hour from 13 November 2025 (13:00 or 14:00 UTC), or the EURUSD or GBPUSD M5 BID Parquet partition from 13 November 2026. Each file is identified by checksum. To resume an interrupted import, choose the same file again.</p>
 <label className="field">Reviewed archive file<input type="file" accept=".bi5,.parquet" onChange={e=>setReviewedFile(e.target.files?.[0]??null)}/></label>
 <button className="btn btn-secondary" disabled={busy||!reviewedFile} onClick={()=>void registerReviewed()}>Register or resume file</button></Card>
 <ArchiveSmtWatch datasets={datasets}/><ArrivalPanel datasets={datasets}/>
 <Card><SectionTitle eyebrow="PROVENANCE INSPECTION" title="Inspect archived candle coverage"/>
   <label className="field"><span>Candle partition</span><div className="select-wrap"><select value={inspected?.id??''}
     onChange={event=>setInspected(datasets.find(item=>item.id===event.target.value)??(initialDataset?.id===event.target.value?initialDataset:undefined))}>
       <option value="">Choose a candle partition</option>{[...new Map([...(initialDataset?[initialDataset]:[]),...datasets].map(item=>[item.id,item])).values()].filter(item=>item.kind==='candles').map(item=>
         <option key={item.id} value={item.id}>{item.symbol} · {item.timeframe} · {item.id.slice(0,12)}</option>)}
   </select></div></label>
   {inspected?.timeframe&&<DataTrust data={trust} symbol={inspected.symbol} timeframe={inspected.timeframe as Timeframe} error={trustError}/>}
 </Card>
 <div className="two-column"><Card><SectionTitle eyebrow="PRIMARY SOURCE" title="Import from MetaTrader 5"/><p className="muted">Read-only import of completed broker bars through the local Windows MT5 terminal.</p><label className="field"><span>Instrument</span><Select value={symbol} onChange={v=>setSymbol(v as SymbolName)} options={['EURUSD','GBPUSD','USDJPY','XAUUSD']}/></label><label className="field"><span>Timeframe</span><Select value={timeframe} onChange={v=>setTimeframe(v as Timeframe)} options={['M1','M3','M5','M15','M30','H1','H4','D1']}/></label><button className="btn btn-primary full" disabled={busy} onClick={()=>void importMT5()}><ArrowDownToLine size={17}/> Import last 500 completed bars</button><small className="muted">Requires MT5 terminal, connected broker account, MetaTrader5 package and pyarrow on Windows.</small></Card>
 <Card><SectionTitle eyebrow="SECONDARY SOURCE" title="Import Dukascopy .bi5"/><p className="muted">Import an existing daily or legacy hourly Dukascopy tick file. No paid bucket access is initiated.</p><label className="field"><span>Source date (UTC)</span><input type="date" value={day} onChange={e=>setDay(e.target.value)}/></label><label className="field"><span>Legacy hourly file? Hour 0–23 (leave blank for daily)</span><input type="number" min="0" max="23" value={hour} onChange={e=>setHour(e.target.value)}/></label><label className="field"><span>Compressed .bi5 file</span><input type="file" accept=".bi5" onChange={e=>setFile(e.target.files?.[0]||null)}/></label><button className="btn btn-secondary full" disabled={!file||busy} onClick={()=>void importBi5()}><Upload size={17}/> Validate & import file</button><small className="muted">Current Dukascopy documented bulk S3 access may incur requester-pays fees. Browser import is free after you obtain a file.</small></Card></div>
 <Card><SectionTitle eyebrow="LOCAL ARCHIVE" title="Verified dataset catalog" action={<Badge tone="blue">{datasets.length} partitions</Badge>}/>{datasetsLoading?<div className="loading-zone" role="status"><Spinner/><span>Loading dataset catalog…</span></div>:<div className="table-scroll"><table><thead><tr><th>SOURCE</th><th>INSTRUMENT</th><th>TYPE</th><th>TIMEFRAME</th><th>RECORDS</th><th>IMPORTED</th></tr></thead><tbody>{datasets.map(d=><tr key={d.id}><td><Badge tone="blue">{d.provider}</Badge></td><td><strong>{d.symbol}</strong></td><td>{d.kind}</td><td>{d.timeframe||'tick'}</td><td>{d.rows_count.toLocaleString()}</td><td>{formatDate(d.created_at)}</td></tr>)}</tbody></table>{!datasets.length&&<Empty icon={<Database/>} title="No real datasets imported" body="Add MT5 candles or a Dukascopy .bi5 file to begin persistent historical research."/>}</div>}</Card>
 </>
}

function SettingsPage(){return <><PageHeading eyebrow="LOCAL ENVIRONMENT / CONFIG" title="Platform settings" subtitle="A minimal, auditable setup that runs on one Windows computer."/><div className="two-column"><Card><SectionTitle eyebrow="RUNTIME" title="Application configuration"/><TimezonePicker/><CandleColorSettings/><WorkspacePresets/><CommandSettings/><div className="settings-list"><div><span>Persistence</span><strong>SQLite (WAL)</strong></div><div><span>Market archive</span><strong>Parquet · Zstandard</strong></div><div><span>Network binding</span><strong>127.0.0.1</strong></div><div><span>AI workflow</span><strong>Manual ZIP / JSON</strong></div><div><span>Order execution</span><Badge tone="green">DISABLED</Badge></div></div></Card><Card><SectionTitle eyebrow="DATA SAFETY" title="Important operational notes"/><div className="settings-notes"><div><ShieldCheck size={18}/><span>Live trading functions are intentionally unavailable.</span></div><div><Archive size={18}/><span>Copy data directory and verify database recovery on a separate storage device.</span></div><div><CloudOff size={18}/><span>Historical research and journal work offline once datasets are imported.</span></div><div><BookOpen size={18}/><span>AI findings are untrusted until checked against source evidence.</span></div></div><a href="http://127.0.0.1:8000/research/docs" className="btn btn-outline full" target="_blank" rel="noreferrer">Open API documentation <ExternalLink size={15}/></a></Card></div></>}

export default App;
