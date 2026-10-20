import assert from "node:assert/strict"
import test from "node:test"
import { recentDirectories } from "./recent-directories.ts"

test("recent directories ignore corrupt data and retain exact path text", () => {
  assert.deepEqual(recentDirectories(null), [])
  assert.deepEqual(recentDirectories([null, 42, "", " ", "C:\\测试数据\\完整路径"]), ["C:\\测试数据\\完整路径"])
})

test("Windows directory aliases deduplicate while the latest spelling wins", () => {
  assert.deepEqual(recentDirectories(["c:/DATA/", "C:\\other", "C:\\data"], "C:\\Data"), ["C:\\Data", "C:\\other"])
})

test("history bounds storage and keeps the most recent six entries", () => {
  assert.deepEqual(recentDirectories(Array.from({ length: 12 }, (_, index) => `C:\\${index}`), "D:\\新目录"), ["D:\\新目录", "C:\\0", "C:\\1", "C:\\2", "C:\\3", "C:\\4"])
})
