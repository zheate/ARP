export type ResultFileOpenOutcome =
  | { status: "opened" }
  | { status: "revealed"; openError: string }

type PathAction = (path: string) => Promise<void>

function errorDetail(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason)
}

export async function openResultFile(
  path: string,
  openFile: PathAction,
  revealFile: PathAction,
): Promise<ResultFileOpenOutcome> {
  try {
    await openFile(path)
    return { status: "opened" }
  } catch (openReason) {
    const openError = errorDetail(openReason)
    try {
      await revealFile(path)
      return { status: "revealed", openError }
    } catch (revealReason) {
      throw new Error(`无法打开结果文件，也无法在文件夹中定位。打开失败：${openError}；定位失败：${errorDetail(revealReason)}`)
    }
  }
}
