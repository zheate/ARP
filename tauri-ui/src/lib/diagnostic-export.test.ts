import assert from "node:assert/strict"
import test from "node:test"
import { diagnosticExportAvailable } from "./diagnostic-export.ts"
import { validateCommandResponse } from "./bridge-validation.ts"

const completed = { state: "completed", detail: "已结束", controlsEnabled: true, terminalOutcome: "succeeded" as const,
  sessionId: "run-a", journalPath: "run-a.jsonl", exportStatus: "completed" }

test("diagnostics are available only for a completed session after file writes settle", () => {
  assert.equal(diagnosticExportAvailable(completed), true)
  assert.equal(diagnosticExportAvailable({ ...completed, exportStatus: "failed" }), true)
  for (const update of [{ state: "waiting_stable" }, { sessionId: undefined }, { journalPath: "" },
    { terminalOutcome: undefined }, { exportStatus: "recording" }, { exportStatus: "exporting" },
    { detail: "正在写入最终测试状态" }]) {
    assert.equal(diagnosticExportAvailable({ ...completed, ...update }), false)
  }
})

test("diagnostic responses require an exact session identity, output path and warning list", () => {
  validateCommandResponse("automatic.exportDiagnostics", { path: "run.zip", sessionId: "run-a", warnings: [] })
  for (const value of [{}, { path: "", sessionId: "a", warnings: [] }, { path: "a.zip", sessionId: "", warnings: [] },
    { path: "a.zip", sessionId: "a", warnings: [null] }, { path: "a.zip", sessionId: "a" }]) {
    assert.throws(() => validateCommandResponse("automatic.exportDiagnostics", value))
  }
})
