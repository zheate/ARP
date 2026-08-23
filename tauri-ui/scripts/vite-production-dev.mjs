// Keep Tauri's hot-reloading development server while serving React's
// production runtime. The React development runtime retains substantially more
// transient render metadata during a continuous measurement stream.
process.env.NODE_ENV = "production"
const parentPid = process.ppid
if (parentPid <= 1) process.exit(0)

const { createServer } = await import("vite")
const server = await createServer()
await server.listen()
server.printUrls()

let closing = false

const close = async () => {
  if (closing) return
  closing = true
  clearInterval(parentWatch)
  await server.close()
  process.exit(0)
}

const parentWatch = setInterval(() => {
  if (process.ppid !== parentPid) {
    void close()
    return
  }
  try {
    process.kill(parentPid, 0)
  } catch (error) {
    if (error?.code === "ESRCH") void close()
  }
}, 250)

process.once("SIGINT", close)
process.once("SIGTERM", close)
