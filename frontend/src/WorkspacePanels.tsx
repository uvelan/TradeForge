import type {CSSProperties, ReactNode} from 'react';
import {useWorkspacePreference} from './useWorkspacePreference';

type Panel = 'watchlist' | 'toolbar' | 'inspector' | 'tools';
const panels = {
  watchlist: {label:'Watchlist', sizes:['160','220','280','340']},
  toolbar: {label:'Toolbar', sizes:['72','112','152','192']},
  inspector: {label:'Evidence Inspector', sizes:['180','280','380','480']},
  tools: {label:'Chart tools', sizes:['44','56','68','80']},
};

function usePanel(id:Panel) {
  const {label,sizes} = panels[id];
  const [size,setSize] = useWorkspacePreference('panel-'+id+'-size','auto',['auto', ...sizes]);
  const [state,setState] = useWorkspacePreference('panel-'+id,'shown',['shown','collapsed']);
  return {id,label,sizes,size,setSize,state,setState};
}

function PanelControls({items}:{items:ReturnType<typeof usePanel>[]}) {
  return <details className="workspace-panel-options"><summary>Panel settings</summary>
    <div>{items.map(({id,label,size,sizes,setSize,state,setState})=><fieldset key={id}>
      <legend>{label}</legend><label><input type="checkbox" aria-label={'Show '+label}
        checked={state==='shown'} onChange={e=>setState(e.target.checked?'shown':'collapsed')}/> Show</label>
      <label>Size <select aria-label={label+' size'} value={size} onChange={e=>setSize(e.target.value)}>
        <option value="auto">Automatic</option>{sizes.map(s=><option key={s} value={s}>{s} px</option>)}
      </select></label></fieldset>)}</div></details>;
}

export function WorkspacePanels({children,inspectorOnly=false}:{children:ReactNode;inspectorOnly?:boolean}) {
  const watch=usePanel('watchlist'), toolbar=usePanel('toolbar'), inspector=usePanel('inspector'), tools=usePanel('tools');
  const items=inspectorOnly?[inspector]:[watch,toolbar,inspector,tools];
  const style=Object.fromEntries(items.filter(p=>p.size!=='auto').map(p=>['--panel-'+p.id,p.size+'px'])) as CSSProperties;
  return <div className={['panel-workspace',...items.filter(p=>p.state==='collapsed').map(p=>'hide-'+p.id)].join(' ')} style={style}>
    <PanelControls items={items}/>{children}</div>;
}
