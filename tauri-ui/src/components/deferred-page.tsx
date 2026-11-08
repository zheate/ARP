import { Component, lazy, memo, Suspense, useState, type ComponentType, type ReactNode } from "react"
import { Button } from "@/components/ui/button"

class PageBoundary extends Component<{ children: ReactNode; onRetry: () => void }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    return this.state.failed ? (
      <div className="page-load-state" role="alert">
        <p>页面暂时无法显示，请重试。</p>
        <Button onClick={this.props.onRetry} variant="outline">重试加载</Button>
      </div>
    ) : this.props.children
  }
}

/** Each page keeps its own loading/error boundary; device controls stay available. */
type PageModule = { source: string; exportName: string }
type Manifest = Record<string, { file: string; css?: string[]; imports?: string[] }>

async function retryPage<Props extends object>({ source, exportName }: PageModule): Promise<{ default: ComponentType<Props> }> {
  let file = source
  if (import.meta.env.PROD) {
    const response = await fetch(new URL("asset-manifest.json", document.baseURI))
    if (!response.ok) throw new Error("无法读取页面资源清单")
    const manifest: Manifest = await response.json()
    const entry = manifest[source]
    if (!entry) throw new Error("页面资源不存在")
    file = entry.file
    const css = new Set<string>()
    const visited = new Set<string>()
    const collect = (key: string) => {
      if (visited.has(key)) return
      visited.add(key)
      manifest[key]?.css?.forEach((file) => css.add(file))
      manifest[key]?.imports?.forEach(collect)
    }
    collect(source)
    await Promise.all([...css].map((file) => new Promise<void>((resolve, reject) => {
      const url = new URL(file, document.baseURI).href
      if ([...document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')].some((link) => link.href === url && link.sheet)) { resolve(); return }
      const link = document.createElement("link")
      link.rel = "stylesheet"
      link.href = url
      link.onload = () => resolve()
      link.onerror = () => { link.remove(); reject(new Error("无法加载页面样式")) }
      document.head.append(link)
    })))
  }
  // A rejected dynamic import can remain cached by the WebView. Use the same
  // built asset with a fresh URL; shared imports still resolve to their original modules.
  const url = new URL(file, document.baseURI)
  url.searchParams.set("pageRetry", crypto.randomUUID())
  const module = await import(/* @vite-ignore */ url.href)
  return { default: module[exportName] as ComponentType<Props> }
}

export function deferredPage<Props extends object>(load: () => Promise<{ default: ComponentType<Props> }>, module: PageModule) {
  const InitialPage = lazy(load)
  return memo(function DeferredPage(props: Props) {
    const [{ Page, attempt }, setAttempt] = useState({ Page: InitialPage, attempt: 0 })
    return (
      <PageBoundary key={attempt} onRetry={() => setAttempt({ Page: lazy(() => retryPage<Props>(module)), attempt: attempt + 1 })}>
        <Suspense fallback={<div className="page-load-state" role="status">正在打开页面…</div>}>
          <Page {...props} />
        </Suspense>
      </PageBoundary>
    )
  })
}
