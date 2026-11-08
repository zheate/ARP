// In-memory UI fixture. No Tauri host, sidecar, or instrument calls.
import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ManualPage } from '../src/features/manual/page'
import { DEMO_SNAPSHOT } from '../src/lib/demo-snapshot'
import '../src/index.css'
import '../src/workbench-appearance.css'
import '../src/operation-motion.css'
const params = new URLSearchParams(location.search)
document.documentElement.classList.toggle('dark', params.get('theme') !== 'light')
const initial = structuredClone(DEMO_SNAPSHOT)
const state = params.get('state') ?? 'disconnected'
initial.backend.mode = 'active'
initial.backend.runtimeId = 'tdk-order-isolated'
initial.configuration.powerSupplyKind = params.get('kind') === 'ch341' ? 'ch341' : 'tdk'
initial.configuration.tdkResource = 'ASRL6::INSTR'
initial.configuration.setCurrentA = 5
initial.devices.powerSupply.connected = state !== 'disconnected'
initial.devices.powerSupply.outputEnabled = ['enabled', 'powered'].includes(state)
initial.devices.powerSupply.outputCurrentA = state === 'disconnected' ? null : state === 'powered' ? 5 : 0
initial.devices.powerSupply.outputVoltageV = state === 'powered' ? 1.8 : 0
initial.safety.manualControlsAllowed = true
initial.safety.shutdown = { requested: false, confirmed: false, measuredCurrentA: null, outputOffConfirmed: false, reason: '' }
initial.manualTest = { pendingCount: 0, exportStatus: 'idle', exportPath: '', exportError: '', capture: { state: 'idle', detail: '', currentA: null, remainingS: null }, currentCommand: { pending: false, stopping: false, requestedCurrentA: null, remainingS: 0 } }
initial.automaticTest.state = 'idle'
const qa = window.__tdkQa = { calls: [], holdApply: false, failStop: false, generation: 0 }
function Harness() {
 const [snapshot, setSnapshot] = useState(initial)
 const [pending, setPending] = useState(false)
 const publish = (change) => setSnapshot(s => { const next = structuredClone(s); change(next); return next })
 qa.patch = publish
 const run = async (method, args = {}) => {
  qa.calls.push({ method, args })
  if (method === 'powerSupply.safeRampDown') {
   qa.generation++
   publish(s => {
    s.devices.powerSupply.outputEnabled = false
    s.devices.powerSupply.outputCurrentA = qa.failStop ? .2 : 0
    s.configuration.setCurrentA = 0
    s.manualTest.currentCommand = { pending: false, stopping: false, requestedCurrentA: null, remainingS: 0 }
    s.safety.shutdown = { requested: true, confirmed: !qa.failStop, measuredCurrentA: qa.failStop ? .2 : 0, outputOffConfirmed: !qa.failStop, reason: qa.failStop ? '无法确认输出电流归零' : '' }
   })
   if (qa.failStop) throw new Error('无法确认输出电流归零')
   return
  }
  setPending(true)
  const generation = qa.generation
  try {
   if (method === 'powerSupply.setCurrent' && qa.holdApply) {
    publish(s => { s.manualTest.currentCommand = { pending: true, stopping: false, requestedCurrentA: args.currentA, remainingS: 1 } })
    await new Promise(resolve => qa.releaseApply = resolve)
   }
   if (generation !== qa.generation) throw new Error('命令已因安全停止失效')
   publish(s => {
    if (method === 'powerSupply.connect') s.devices.powerSupply.connected = true
    if (method === 'powerSupply.disconnect') s.devices.powerSupply.connected = false
    if (method === 'powerSupply.setOutput') { s.devices.powerSupply.outputEnabled = args.enabled; s.configuration.setCurrentA = 0; s.devices.powerSupply.outputCurrentA = 0; s.safety.shutdown.requested = false }
    if (method === 'powerSupply.setCurrent') { s.devices.powerSupply.outputCurrentA = args.currentA; s.configuration.setCurrentA = args.currentA; s.safety.shutdown.requested = false; s.manualTest.currentCommand.pending = false }
   })
  } finally { setPending(false) }
 }
 return <main style={{ padding: 16 }}><ManualPage snapshot={snapshot} config={snapshot.configuration} active pending={pending} run={run} update={(key, value) => publish(s => s.configuration[key] = value)} /></main>
}
createRoot(document.getElementById('root')).render(<Harness />)
