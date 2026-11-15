import {createContext,useContext,type ReactNode} from "react";
import {useWorkspacePreference} from "./useWorkspacePreference";

const defaults={up:"#2cd8ae",down:"#ea6a80",comparisonUp:"#74a2ff",comparisonDown:"#c393f6"};
const ColorContext=createContext({colors:defaults,setColor:(_key:keyof typeof defaults,_value:string)=>{}});

export function CandleColorsProvider({children}:{children:ReactNode}){
  const [up,setUp]=useWorkspacePreference<string>("candles-up",defaults.up);
  const [down,setDown]=useWorkspacePreference<string>("candles-down",defaults.down);
  const [comparisonUp,setComparisonUp]=useWorkspacePreference<string>("comparison-up",defaults.comparisonUp);
  const [comparisonDown,setComparisonDown]=useWorkspacePreference<string>("comparison-down",defaults.comparisonDown);
  const setters={up:setUp,down:setDown,comparisonUp:setComparisonUp,comparisonDown:setComparisonDown};
  const colors={
    up:validHex(up)?up:defaults.up,
    down:validHex(down)?down:defaults.down,
    comparisonUp:validHex(comparisonUp)?comparisonUp:defaults.comparisonUp,
    comparisonDown:validHex(comparisonDown)?comparisonDown:defaults.comparisonDown,
  };
  function setColor(key:keyof typeof defaults,value:string){
    if(validHex(value))setters[key](value);
  }
  return <ColorContext.Provider value={{colors,setColor}}>{children}</ColorContext.Provider>;
}

const validHex=(value:string)=>/^#[0-9a-f]{6}$/i.test(value);
export const useCandleColors=()=>useContext(ColorContext).colors;

export function CandleColorSettings(){
  const {colors,setColor}=useContext(ColorContext);
  const fields=[
    ["up","Rising candle"],["down","Falling candle"],
    ["comparisonUp","Rising comparison candle"],["comparisonDown","Falling comparison candle"],
  ] as const;
  return <fieldset className="candle-color-settings">
    <legend>Candle colors</legend>
    {fields.map(([key,label])=><label key={key}>
      <span>{label}</span>
      <input type="color" aria-label={label} value={colors[key]} onChange={event=>setColor(key,event.target.value)}/>
      <code aria-hidden="true">{colors[key].toUpperCase()}</code>
    </label>)}
    <button type="button" className="btn btn-outline" onClick={()=>fields.forEach(([key])=>setColor(key,defaults[key]))}>
      Reset candle colors
    </button>
  </fieldset>;
}
