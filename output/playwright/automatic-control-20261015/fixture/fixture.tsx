import { createRoot } from 'react-dom/client'
import { useState } from 'react'
import { AutomaticPage } from '@/features/automatic/page'
import { AutomaticTestControlCard } from '@/features/shared/controls'
import { DEMO_SNAPSHOT } from '@/lib/demo-snapshot'

const query = new URLSearchParams(location.search)
const noop = () => undefined
const asyncNoop = async () => undefined
const theme = query.get('theme') || 'light'
document.documentElement.dataset.theme = theme
document.documentElement.dataset.accent = 'rose'
document.documentElement.dataset.density = query.get('density') || 'comfortable'
document.documentElement.classList.toggle('dark', theme === 'dark')

function snapshotFor(state: string) {
  const snapshot = structuredClone(DEMO_SNAPSHOT)
  const auto = snapshot.automaticTest
  auto.elapsedS = state === 'long' ? 367199 : 156
  auto.savedPointCount = state === 'long' ? 123456 : 3
  auto.state = ['done', 'uncertain', 'success', 'failed'].includes(state) ? 'completed'
    : state === 'idle' ? 'idle' : state === 'paused' ? 'paused'
      : state === 'ramping' ? 'ramping_down' : 'waiting_stable'
  auto.terminalOutcome = state === 'uncertain' ? 'shutdown_unconfirmed'
    : state === 'done' ? 'stopped_shutdown_confirmed'
      : state === 'success' ? 'succeeded' : state === 'failed' ? 'aborted_shutdown_confirmed' : null
  auto.canStart = auto.state === 'idle' || state === 'done' || state === 'success'
  auto.canEnd = !['idle', 'completed'].includes(auto.state)
  snapshot.safety.shutdown.confirmed = ['done', 'success', 'failed'].includes(state)
  return snapshot
}

function Fixture() {
  const [state, setState] = useState(query.get('state') || 'done')
  const [pending, setPending] = useState(false)
  const [active, setActive] = useState(true)
  const snapshot = snapshotFor(state)
  const commands: string[] = (window as any).__qaCommands ||= []
  const run = async (method: string) => { commands.push(method); return snapshot }
  ;(window as any).__controlQA = { setState, setPending, setActive }
  if (query.get('component') === '1') return <section className="automatic-device-rail" style={{width:296,height:112,gridTemplateColumns:'1fr',margin:24}}>
    <AutomaticTestControlCard active={active} auto={snapshot.automaticTest} config={snapshot.configuration!}
      controlsLocked={!['idle','completed'].includes(snapshot.automaticTest.state) || state==='uncertain'}
      pending={pending} shutdown={snapshot.safety.shutdown} onStart={()=>commands.push('start')} onStop={()=>commands.push('stop')}/>
  </section>
  return <div className="app-shell" style={{display:'block',height:'100dvh',padding:'16px',paddingLeft:innerWidth > 1100 ? '180px' : '16px'}}>
    <main data-page="automatic" style={{height:'100%',display:'flex',flexDirection:'column',gap:8,padding:0}}>
      <AutomaticPage toolbarHost={null} snapshot={snapshot} config={snapshot.configuration!}
        update={noop} active={active} dirty={false} controlsLocked={!['idle','completed'].includes(snapshot.automaticTest.state) || state==='uncertain'}
        curveLineStyle="solid" latestPointMarkerStyle="point" pending={pending} run={run as any}
        saveConfiguration={asyncNoop} testScripts={snapshot.testScripts || []}
        onScriptSelect={noop} onCreateScript={asyncNoop} onDeleteScript={asyncNoop}
        onNextDevice={noop} onViewData={noop} onRetryExport={noop} onChooseExportDirectory={noop} onShowCompletion={noop}/>
    </main>
  </div>
}

createRoot(document.getElementById('root')!).render(<Fixture/>)
