// Keep Tauri's hot-reloading development server while serving React's
// production runtime. The React development runtime retains substantially more
// transient render metadata during a continuous measurement stream.
process.env.NODE_ENV = "production"

const { createServer } = await import("vite")
const server = await createServer()
await server.listen()
server.printUrls()

const close = async () => {
  await server.close()
  process.exit(0)
}

process.once("SIGINT", close)
process.once("SIGTERM", close)
