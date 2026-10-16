import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import { normalizeBridgeError } from "./bridge-error.ts"
import { commandRuntimeIsCurrent, validateCommandResponse, validateSnapshot } from "./bridge-validation.ts"
const fixture = JSON.parse(readFileSync(new URL("../../../tests/fixtures/bridge-contract.json", import.meta.url), "utf8"))
test("shared protocol samples validate handshake, shutdown and snapshot", () => {
  validateCommandResponse("system.ping", fixture.pingResponse.result)
  validateCommandResponse("app.shutdown", fixture.shutdown)
  validateSnapshot(fixture.snapshot)
  assert.throws(() => validateCommandResponse("system.ping", { ...fixture.pingResponse.result, protocolVersion: 1 }), /版本/)
  assert.throws(() => validateCommandResponse("app.shutdown", { ...fixture.shutdown, shutdownConfirmed: true }), /格式/)
  assert.throws(() => validateSnapshot({ ...fixture.snapshot, devices: {} }), /格式/)
})
test("structured failures preserve identity and legacy text stays readable", () => {
  const error = normalizeBridgeError({ ...fixture.errorResponse.error, kind: "backend", requestId: fixture.errorResponse.id }, "powerSupply.setCurrent")
  assert.equal(error.failure.code, "device_not_connected")
  assert.equal(error.failure.requestId, "contract-2")
  assert.equal(error.failure.runtimeId, "contract-runtime")
  assert.equal(normalizeBridgeError("旧版错误").message, "旧版错误")
})
test("old command replies cannot overwrite a restarted backend", () => {
  assert.equal(commandRuntimeIsCurrent("old", "new", "old"), false)
  assert.equal(commandRuntimeIsCurrent("old", "new", "new"), true)
  assert.equal(commandRuntimeIsCurrent("same", "same", "same"), true)
})
