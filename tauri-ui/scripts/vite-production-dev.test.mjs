import assert from "node:assert/strict"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import { spawn } from "node:child_process"
import test from "node:test"
import { fileURLToPath } from "node:url"

const scriptPath = fileURLToPath(new URL("./vite-production-dev.mjs", import.meta.url))

const delay = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds))

const processIsAlive = (pid) => {
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    if (error?.code === "ESRCH") return false
    throw error
  }
}

const stopProcess = (pid) => {
  if (!processIsAlive(pid)) return
  try {
    process.kill(pid, "SIGTERM")
  } catch (error) {
    if (error?.code !== "ESRCH") throw error
  }
}

const waitForProcessExit = async (pid, timeoutMilliseconds = 2000) => {
  const deadline = Date.now() + timeoutMilliseconds
  while (processIsAlive(pid) && Date.now() < deadline) {
    await delay(50)
  }
}

test("dev server exits after its launcher process disappears", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "arp-vite-parent-exit-"))
  let serverPid

  try {
    const launcherSource = [
      "const { spawn } = require('node:child_process')",
      `const child = spawn(process.execPath, [${JSON.stringify(scriptPath)}], {`,
      `  cwd: ${JSON.stringify(root)},`,
      "  detached: true,",
      "  stdio: 'ignore',",
      "  windowsHide: true,",
      "})",
      "console.log(child.pid)",
      "child.unref()",
    ].join("\n")

    const launcher = spawn(process.execPath, ["-e", launcherSource], {
      stdio: ["ignore", "pipe", "inherit"],
      windowsHide: true,
    })
    let stdout = ""
    launcher.stdout.setEncoding("utf8")
    launcher.stdout.on("data", (chunk) => {
      stdout += chunk
    })
    const launcherExitCode = await new Promise((resolve, reject) => {
      launcher.once("error", reject)
      launcher.once("exit", resolve)
    })
    assert.equal(launcherExitCode, 0)
    serverPid = Number.parseInt(stdout.trim(), 10)
    assert.ok(Number.isInteger(serverPid), `invalid child pid: ${stdout}`)

    await delay(1500)

    assert.equal(
      processIsAlive(serverPid),
      false,
      `orphaned Vite process ${serverPid} is still running`,
    )
  } finally {
    if (serverPid) {
      stopProcess(serverPid)
      await waitForProcessExit(serverPid)
    }
    await rm(root, { recursive: true, force: true })
  }
})
