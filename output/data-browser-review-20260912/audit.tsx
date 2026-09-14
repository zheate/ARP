import React from 'react';
import ReactDOM from 'react-dom/client';
import { DataBrowserPage } from '/src/components/data-browser-page';
import { AutomaticCompletionSummary } from '/src/components/automatic-overview';
import '/src/index.css';
import '/src/workbench-appearance.css';
import '/src/components/data-browser.css';
window.__TAURI_INTERNALS__ = {invoke:async(cmd,args)=>{
 if(cmd !== 'bridge_request')throw Error('审查模式仅支持读取数据');
 const res=await fetch('/__data',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(args)});
 const value=await res.json();if(!res.ok)throw Error(value.error);return value;
}};
const noop=()=>{};
function Review(){
 const [page,setPage]=React.useState('data');
 const cfg={sn:'QA-SN-001',station:'封盖后',testTemperatureC:'25 ℃',selectedScriptId:'default'};
 const snap={automaticTest:{state:'completed',terminalOutcome:'succeeded',runContext:cfg,savedPointCount:6,currents:[1,2,3,4,5,6],elapsedS:185,exportStatus:'completed',exportPath:'/data/QA-SN-001.xlsx',canNextDevice:true},safety:{shutdown:{confirmed:true,measuredCurrentA:0}},measurements:{stable:[],agingPower:[]}};
 return <div className="app-shell flex h-screen flex-col overflow-hidden bg-background text-foreground"><header style={{height:46,padding:'0 20px',display:'flex',alignItems:'center',justifyContent:'space-between',borderBottom:'1px solid var(--border)'}}><strong>Power Test</strong><small style={{color:'var(--muted-foreground)'}}>当前组件审查 · 测试文件 / 示例完成状态</small></header><div className="app-shell__body grid min-h-0 flex-1 overflow-hidden"><aside className="app-shell__sidebar" style={{padding:12,borderRight:'1px solid var(--border)'}}>{[['automatic','自动测试'],['data','数据浏览']].map(([id,label])=><button className="app-nav-item" key={id} onClick={()=>setPage(id)} style={{width:'100%',padding:'12px 16px',textAlign:'left',background:page===id?'var(--muted)':'transparent'}}>{label}</button>)}</aside><main style={{padding:16,minHeight:0,minWidth:0}}>{page==='data'?<DataBrowserPage visible enabled defaultDirectory="/Users/zh/Documents/test/ARP/output/data-browser-qa/records"/>:<AutomaticCompletionSummary snapshot={snap as any} config={cfg as any} active pending={false} onNextDevice={noop} onViewData={()=>setPage('data')} onRetryExport={noop} onChooseExportDirectory={noop} onShowDetails={noop}/>}</main></div></div>;
}
ReactDOM.createRoot(document.getElementById('root')!).render(<Review/>);
