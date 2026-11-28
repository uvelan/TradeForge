import {createContext,useCallback,useContext,useEffect,useMemo,useRef,useState,type CSSProperties,type PointerEvent as ReactPointerEvent,type ReactNode} from 'react';
import {PanelLeft,PanelRight,RotateCcw} from 'lucide-react';
import {useWorkspacePreference} from './useWorkspacePreference';
import {clampDockWidth,getDockBounds,normalizeDockSide,stepDockWidthForKey,type DockSide} from './workspaceLayout';

type Panel = 'watchlist' | 'toolbar' | 'inspector' | 'tools';
const panels = {
  watchlist: {label:'Watchlist', sizes:['160','220','280','340']},
  toolbar: {label:'Toolbar', sizes:['72','112','152','192']},
  inspector: {label:'Evidence Inspector', sizes:['180','280','380','480']},
  tools: {label:'Chart tools', sizes:['44','56','68','80']},
};

const DOCK_WIDTH_KEY = 'tradeforge.workspace.dock-width';

function readStoredDockWidth(): string {
  try {
    return localStorage.getItem(DOCK_WIDTH_KEY) ?? 'auto';
  } catch {
    return 'auto';
  }
}

function writeStoredDockWidth(value: string) {
  try {
    if (value === 'auto') localStorage.removeItem(DOCK_WIDTH_KEY);
    else localStorage.setItem(DOCK_WIDTH_KEY, value);
  } catch {}
}

type WorkspaceDockContextValue = {
  dockSide: DockSide;
  collapsed: boolean;
  clampedWidth: number;
  bounds: { min: number; max: number; defaultWidth: number };
  dragging: boolean;
  setDragging: (active: boolean) => void;
  setDockWidthPx: (width: number) => void;
  stepWidthByKey: (key: string, shiftKey: boolean) => boolean;
  containerWidth: number;
};

const WorkspaceDockContext = createContext<WorkspaceDockContextValue | null>(null);

function usePanel(id:Panel) {
  const {label,sizes} = panels[id];
  const [size,setSize] = useWorkspacePreference('panel-'+id+'-size','auto',['auto', ...sizes]);
  const [state,setState] = useWorkspacePreference('panel-'+id,'shown',['shown','collapsed']);
  return {id,label,sizes,size,setSize,state,setState};
}

function PanelControls({items,dockControls}:{
  items:ReturnType<typeof usePanel>[];
  dockControls?:{
    dockSide:DockSide;
    setDockSide:(side:DockSide)=>void;
    collapsed:boolean;
    toggleCollapsed:()=>void;
    resetLayout:()=>void;
    onPresetSize:(value:string)=>void;
  };
}) {
  return <div className="workspace-panel-bar">
    <details className="workspace-panel-options"><summary>Panel settings</summary>
      <div>{items.map(({id,label,size,sizes,setSize,state,setState})=><fieldset key={id}>
        <legend>{label}</legend><label><input type="checkbox" aria-label={'Show '+label}
          checked={state==='shown'} onChange={e=>setState(e.target.checked?'shown':'collapsed')}/> Show</label>
        <label>Size <select aria-label={label+' size'} value={size} onChange={e=>{setSize(e.target.value);if(dockControls&&(id==='inspector'||id==='watchlist'))dockControls.onPresetSize(e.target.value);}}>
          <option value="auto">Automatic</option>{sizes.map(s=><option key={s} value={s}>{s} px</option>)}
        </select></label></fieldset>)}
        {dockControls&&<fieldset><legend>Inspector dock</legend>
          <label>Dock side <select aria-label="Inspector dock side" value={dockControls.dockSide}
            onChange={e=>dockControls.setDockSide(normalizeDockSide(e.target.value))}>
            <option value="right">Right</option>
            <option value="left">Left</option>
          </select></label>
        </fieldset>}
      </div></details>
    {dockControls&&<div className="workspace-dock-controls" role="group" aria-label="Desktop panel controls">
      <button type="button" className="small-tool" aria-label="Dock inspector left"
        aria-pressed={dockControls.dockSide==='left'}
        onClick={()=>dockControls.setDockSide('left')}><PanelLeft size={14}/> Dock left</button>
      <button type="button" className="small-tool" aria-label="Dock inspector right"
        aria-pressed={dockControls.dockSide==='right'}
        onClick={()=>dockControls.setDockSide('right')}><PanelRight size={14}/> Dock right</button>
      <button type="button" className="small-tool" aria-expanded={!dockControls.collapsed}
        onClick={dockControls.toggleCollapsed}>{dockControls.collapsed?'Restore inspector':'Collapse inspector'}</button>
      <button type="button" className="small-tool" onClick={dockControls.resetLayout}><RotateCcw size={13}/> Reset layout</button>
    </div>}
  </div>;
}

export function WorkspaceSplitter() {
  const dock = useContext(WorkspaceDockContext);
  const dragOrigin = useRef<{ pointerId: number; startX: number; startWidth: number } | null>(null);
  if (!dock || dock.collapsed) return null;

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || !dock) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragOrigin.current = { pointerId: event.pointerId, startX: event.clientX, startWidth: dock.clampedWidth };
    dock.setDragging(true);
  }

  function onPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const origin = dragOrigin.current;
    if (!origin || origin.pointerId !== event.pointerId || !dock) return;
    const dx = event.clientX - origin.startX;
    const nextWidth = origin.startWidth + (dock.dockSide === 'right' ? -dx : dx);
    dock.setDockWidthPx(nextWidth);
  }

  function endDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const origin = dragOrigin.current;
    if (!origin || origin.pointerId !== event.pointerId || !dock) return;
    dragOrigin.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    dock.setDragging(false);
  }

  return <div
    className={'workspace-splitter' + (dock.dragging ? ' dragging' : '')}
    role="separator"
    aria-orientation="vertical"
    aria-label="Resize inspector panel"
    aria-valuemin={dock.bounds.min}
    aria-valuemax={dock.bounds.max}
    aria-valuenow={dock.clampedWidth}
    tabIndex={0}
    title="Drag or use Left/Right arrow keys to resize inspector"
    onPointerDown={onPointerDown}
    onPointerMove={onPointerMove}
    onPointerUp={endDrag}
    onPointerCancel={endDrag}
    onKeyDown={event => {
      if (!dock.stepWidthByKey(event.key, event.shiftKey)) return;
      event.preventDefault();
      event.stopPropagation();
    }}
  />;
}

export function WorkspacePanels({children,inspectorOnly=false}:{children:ReactNode;inspectorOnly?:boolean}) {
  const watch=usePanel('watchlist'), toolbar=usePanel('toolbar'), inspector=usePanel('inspector'), tools=usePanel('tools');
  const [dockSide, setDockSide] = useWorkspacePreference<DockSide>('dock-side', 'right', ['right', 'left']);
  const [rawDockWidth, setRawDockWidth] = useState<string>(readStoredDockWidth);
  const [dragging, setDragging] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState<number>(() =>
    typeof window !== 'undefined' ? window.innerWidth : 1280,
  );

  useEffect(() => {
    const measure = () => {
      const w = rootRef.current?.clientWidth || window.innerWidth;
      if (w > 0) setContainerWidth(w);
    };
    measure();
    window.addEventListener('resize', measure);
    const observer = typeof ResizeObserver !== 'undefined' && rootRef.current
      ? new ResizeObserver(measure) : undefined;
    if (observer && rootRef.current) observer.observe(rootRef.current);
    return () => {
      window.removeEventListener('resize', measure);
      observer?.disconnect();
    };
  }, []);

  const setDockWidthPx = useCallback((width: number) => {
    const next = String(clampDockWidth(width, containerWidth));
    setRawDockWidth(next);
    writeStoredDockWidth(next);
  }, [containerWidth]);

  const effectiveSourceWidth = rawDockWidth !== 'auto'
    ? rawDockWidth
    : inspector.size !== 'auto'
      ? inspector.size
      : watch.size;
  const bounds = useMemo(() => getDockBounds(containerWidth), [containerWidth]);
  const clampedWidth = useMemo(
    () => clampDockWidth(effectiveSourceWidth, containerWidth),
    [effectiveSourceWidth, containerWidth],
  );

  const stepWidthByKey = useCallback((key: string, shiftKey: boolean) => {
    const next = stepDockWidthForKey(key, shiftKey, clampedWidth, dockSide, containerWidth);
    if (next === undefined) return false;
    setDockWidthPx(next);
    return true;
  }, [clampedWidth, dockSide, containerWidth, setDockWidthPx]);

  const items=inspectorOnly?[inspector]:[watch,toolbar,inspector,tools];
  const collapsed = inspector.state === 'collapsed';

  const resetLayout = useCallback(() => {
    for (const panel of items) {
      panel.setSize('auto');
      panel.setState('shown');
    }
    setDockSide('right');
    setRawDockWidth('auto');
    writeStoredDockWidth('auto');
  }, [items, setDockSide]);

  const dockValue = useMemo<WorkspaceDockContextValue>(() => ({
    dockSide,
    collapsed,
    clampedWidth,
    bounds,
    dragging,
    setDragging,
    setDockWidthPx,
    stepWidthByKey,
    containerWidth,
  }), [dockSide, collapsed, clampedWidth, bounds, dragging, setDockWidthPx, stepWidthByKey, containerWidth]);

  const style={
    ...Object.fromEntries(items.filter(p=>p.size!=='auto').map(p=>['--panel-'+p.id,p.size+'px'])),
    '--panel-dock-width': `${clampedWidth}px`,
  } as CSSProperties;

  return <WorkspaceDockContext.Provider value={dockValue}>
    <div
      ref={rootRef}
      className={['panel-workspace',...items.filter(p=>p.state==='collapsed').map(p=>'hide-'+p.id)].join(' ')}
      data-dock-side={dockSide}
      style={style}
    >
      <PanelControls
        items={items}
        dockControls={inspectorOnly ? undefined : {
          dockSide,
          setDockSide,
          collapsed,
          toggleCollapsed: () => inspector.setState(collapsed ? 'shown' : 'collapsed'),
          resetLayout,
          onPresetSize: (value: string) => {
            setRawDockWidth(value);
            writeStoredDockWidth(value);
          },
        }}
      />
      {children}
    </div>
  </WorkspaceDockContext.Provider>;
}
