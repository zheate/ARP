// Isolated browser fixture. All command and save ports are in memory.
import {useState} from 'react'
import {createRoot} from 'react-dom/client'
import {flushSync} from 'react-dom'
import {DeviceSettingsDialog} from '../../src/features/devices/settings'
import {PowerProtectionDialog, ShutdownFailureDialog} from '../../src/features/automatic/lifecycle-dialogs'
import {AutomaticTestControlCard, RealtimeMetricCards} from '../../src/features/shared/controls'
import {CanvasLineChart} from '../../src/components/canvas-line-chart'
import {Button} from '../../src/components/ui/button'
import {Select,SelectTrigger,SelectValue,SelectContent,SelectItem} from '../../src/components/ui/select'
import {DEMO_RUN_SNAPSHOT} from '../../src/lib/demo-snapshot'
import {demoRunSnapshot,DEMO_RUN_DURATION_S} from '../../src/lib/demo-run'
import {mergeBackendSnapshot} from '../../src/lib/backend'
import {measurementVerdict} from '../../src/lib/measurement-verdict'
const commands=[],saved=[],frames=[],timings=[]
let current=structuredClone(DEMO_RUN_SNAPSHOT),seq=0
const capture=(method,params={})=>{commands.push({id:commands.length+1,method,params});return Promise.resolve()}
function Harness(){
 const [kind,setKind]=useState(''),[safety,setSafety]=useState(''),[running,setRunning]=useState(false),[snapshot,setSnapshot]=useState(current),[config,setConfig]=useState(current.configuration)
 window.__motionQA={commands,saved,frames,timings,
  advance(index){const started=performance.now();seq=index;current=mergeBackendSnapshot(current,demoRunSnapshot(DEMO_RUN_SNAPSHOT,DEMO_RUN_DURATION_S*index/100));
   frames.push({seq,index:current.automaticTest.currentIndex,revisions:current.seriesRevisions,raw:current.measurements,devices:current.devices,auto:current.automaticTest,verdict:measurementVerdict(current.devices.powerMeter.powerW,'live',true)});
   flushSync(()=>setSnapshot(current));timings.push({seq,renderMs:performance.now()-started});},
  result(){return {commands,saved,frames}},
 }
 return <main style={{padding:16}}><div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
  {['powerSupply','powerMeter','spectrometer'].map(k=><Button data-motion="feedback" id={'open-'+k} key={k} onClick={()=>setKind(k)}>{k} 设置</Button>)}
  <Button id="protection" onClick={()=>setSafety('protection')}>保护夹具</Button><Button id="shutdown" onClick={()=>setSafety('shutdown')}>下电失败夹具</Button>
  <Button id="disabled-command" disabled onClick={()=>capture('must-not-run')}>不可用命令</Button>
  <Button id="save-records" data-motion="feedback" onClick={()=>saved.push(JSON.stringify(current.measurements))}>保存内存记录</Button>
  <Select defaultValue="one"><SelectTrigger id="radix-trigger"><SelectValue/></SelectTrigger><SelectContent position="popper"><SelectItem value="one">选项一</SelectItem><SelectItem value="two">选项二</SelectItem></SelectContent></Select>
 </div>
 <AutomaticTestControlCard active auto={{...snapshot.automaticTest,canEnd:running}} config={config} controlsLocked={false} pending={false} onStart={()=>{capture('automatic.start');setRunning(true)}} onStop={()=>{capture('automatic.end');setRunning(false)}}/>
 <RealtimeMetricCards config={config} automatic={snapshot.automaticTest} powerMeter={snapshot.devices.powerMeter} spectrometer={snapshot.devices.spectrometer}/>
 <div style={{height:220,width:'100%'}}><CanvasLineChart ariaLabel="动效回放曲线" data={snapshot.measurements.power} lines={[{key:'powerW',label:'原始功率',color:'var(--chart-1)'}]} xKey="elapsedS"/></div>
 {kind&&<DeviceSettingsDialog kind={kind} open onClose={()=>setKind('')} onSave={()=>{saved.push(JSON.stringify(config));return capture('configuration.save',config)}} snapshot={snapshot} config={config} update={(key,value)=>setConfig(c=>({...c,[key]:value}))} active pending={false} run={capture}/>}
 {safety==='protection'&&<PowerProtectionDialog message="隔离夹具：保护触发，请核实实体面板。" onClose={()=>setSafety('')}/>}
 {safety==='shutdown'&&<ShutdownFailureDialog message="隔离夹具：下电未确认。" shutdownConfirmed={false} pending={false} onCancel={()=>setSafety('')} onRetry={()=>capture('shutdown.retry')} onForceExit={()=>capture('shutdown.force')}/>}
 </main>
}
createRoot(document.getElementById('root')).render(<Harness/>);
