export interface FullscreenPort {
  isFullscreen: () => boolean | Promise<boolean>
  enter: () => Promise<void>
  exit: () => Promise<void>
}

/** Restore only fullscreen acquired by this presentation. */
export async function acquirePresentationFullscreen(port: FullscreenPort): Promise<() => Promise<void>> {
  if (await port.isFullscreen()) return async () => {}
  await port.enter()
  let released = false
  return async () => {
    if (released) return
    if (await port.isFullscreen()) await port.exit()
    released = true
  }
}
