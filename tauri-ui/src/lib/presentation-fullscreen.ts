export interface FullscreenPort {
  isFullscreen: () => boolean | Promise<boolean>
  enter: () => Promise<void>
  exit: () => Promise<void>
}

export interface PresentationWindowPort extends FullscreenPort {
  isMaximized: () => boolean | Promise<boolean>
  unmaximize: () => Promise<void>
  maximize: () => Promise<void>
}

/** Windows must leave its maximized work area before entering monitor fullscreen. */
export async function acquireWindowPresentationFullscreen(port: PresentationWindowPort): Promise<() => Promise<void>> {
  if (await port.isFullscreen()) return async () => {}
  const wasMaximized = await port.isMaximized()
  let restoreFullscreen: () => Promise<void>
  try {
    if (wasMaximized) await port.unmaximize()
    restoreFullscreen = await acquirePresentationFullscreen(port)
  } catch (error) {
    if (wasMaximized) await port.maximize()
    throw error
  }
  let released = false
  return async () => {
    if (released) return
    await restoreFullscreen()
    if (wasMaximized) await port.maximize()
    released = true
  }
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
