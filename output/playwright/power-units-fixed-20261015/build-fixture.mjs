// Browser-only QA: real pages, simulated snapshots, inert device callbacks.
import { createRequire } from 'node:module';
const require = createRequire(`${process.cwd()}/package.json`);
const { build } = require('esbuild');
await build({
  stdin: { resolveDir: process.cwd(), loader: 'tsx', contents: `
import {createRoot} from 'react-dom/client';import {flushSync} from 'react-dom';
import {ManualPage} from './src/features/manual/page';import {AutomaticPage} from './src/features/automatic/page';
import {AppearanceProvider} from './src/hooks/use-appearance';import {DEFAULT_APPEARANCE,APPEARANCE_STORAGE_KEY} from './src/lib/appearance';
import {DEMO_SNAPSHOT} from './src/lib/demo-snapshot';
localStorage.setItem(APPEARANCE_STORAGE_KEY,JSON.stringify({...DEFAULT_APPEARANCE,highlightChanges:true}));
const main=document.querySelector('main');const style=document.createElement('style');style.textContent='main > :not(#power-units-qa){display:none!important}';document.head.append(style);
const mount=document.createElement('div');mount.id='power-units-qa';Object.assign(mount.style,{display:'flex',flexDirection:'column',flex:'1 1 0',minHeight:'0',minWidth:'0'});main.append(mount);const root=createRoot(mount);
const noop=()=>{};const asyncNoop=async()=>{};
window.renderPowerUnitsCase=(mode,power,current,state='waiting_stable',empty=false)=>{
 const snapshot=structuredClone(DEMO_SNAPSHOT);snapshot.configuration.powerSupplyKind='tdk';snapshot.safety.manualControlsAllowed=true;
 snapshot.devices.powerMeter={...snapshot.devices.powerMeter,powerW:power,quality:power===null?'missing':'live'};
 snapshot.devices.powerSupply={...snapshot.devices.powerSupply,connected:true,outputCurrentA:current,activeCurrentA:current};
 snapshot.automaticTest={...snapshot.automaticTest,state:mode==='manual'?'idle':state,currentA:current,terminalOutcome:null};if(empty)snapshot.measurements.power=[];
 main.dataset.page=mode==='manual'?'manual':'automatic';
 const common={snapshot,config:snapshot.configuration,update:noop,active:true,pending:false,run:asyncNoop,saveConfiguration:asyncNoop};
 flushSync(()=>root.render(<AppearanceProvider>{mode==='manual'?<ManualPage {...common}/>:<AutomaticPage {...common} toolbarHost={null} dirty={false} controlsLocked={false} curveLineStyle="solid" latestPointMarkerStyle="point" testScripts={[]} onScriptSelect={noop} onCreateScript={asyncNoop} onDeleteScript={asyncNoop} onNextDevice={noop} onViewData={noop} onRetryExport={noop} onChooseExportDirectory={noop} onShowCompletion={noop}/>}</AppearanceProvider>));
};
window.renderPowerUnitsCase('automatic',0.86,8);
` },
  bundle: true, format: 'iife', jsx: 'automatic',
  // The running app already loaded the production styles, including manual controls.
  loader: { '.css': 'empty' },
  define: { 'process.env.NODE_ENV': '"production"' },
  outfile: '../output/playwright/power-units-fixed-20261015/site/__qa/power-unit-pages.js',
});
