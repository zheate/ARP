import assert from "node:assert/strict"
import test from "node:test"

import { openResultFile } from "./result-file.ts"

test("opens the result with the default application", async () => {
  const calls: string[] = []
  const outcome = await openResultFile(
    "C:/results/test.xlsx",
    async (path) => { calls.push(`open:${path}`) },
    async (path) => { calls.push(`reveal:${path}`) },
  )

  assert.deepEqual(outcome, { status: "opened" })
  assert.deepEqual(calls, ["open:C:/results/test.xlsx"])
})

test("reveals the result when the default application cannot open it", async () => {
  const calls: string[] = []
  const outcome = await openResultFile(
    "C:/results/test.xlsx",
    async (path) => {
      calls.push(`open:${path}`)
      throw new Error("没有可用的 Excel 程序")
    },
    async (path) => { calls.push(`reveal:${path}`) },
  )

  assert.deepEqual(outcome, { status: "revealed", openError: "没有可用的 Excel 程序" })
  assert.deepEqual(calls, ["open:C:/results/test.xlsx", "reveal:C:/results/test.xlsx"])
})

test("reports both errors when opening and revealing fail", async () => {
  await assert.rejects(
    openResultFile(
      "C:/results/missing.xlsx",
      async () => { throw new Error("文件不存在") },
      async () => { throw new Error("资源管理器不可用") },
    ),
    /打开失败：文件不存在；定位失败：资源管理器不可用/,
  )
})
