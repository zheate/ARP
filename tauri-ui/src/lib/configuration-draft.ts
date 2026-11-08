/** Versioned UI draft. Backend refreshes never overwrite unsaved edits. */
export class ConfigurationDraft<T> {
  private state: { value: T; dirty: boolean; hydrated: boolean; revision: number }
  private pending: T | null = null
  private listeners = new Set<() => void>()

  private equal: (a: T, b: T) => boolean
  constructor(initial: T, equal: (a: T, b: T) => boolean) {
    this.equal = equal
    this.state = { value: initial, dirty: false, hydrated: false, revision: 0 }
  }

  getSnapshot = () => this.state
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }
  private publish(next: typeof this.state) {
    this.state = next
    for (const listener of this.listeners) listener()
  }
  edit(value: T) {
    this.publish({ ...this.state, value, dirty: true, revision: this.state.revision + 1 })
  }
  hydrate(value: T) {
    if (this.state.dirty || (this.pending && !this.equal(value, this.pending))) return
    this.pending = null
    if (this.state.hydrated && this.equal(value, this.state.value)) return
    this.publish({ ...this.state, value, hydrated: true })
  }
  acknowledge(revision: number, value: T) {
    if (revision !== this.state.revision) return false
    this.pending = value
    this.publish({ ...this.state, value, dirty: false, hydrated: true })
    return true
  }
  /** Only backend-owned derived fields may be merged into an unsaved draft. */
  mergeAuthoritative(merge: (value: T) => T) {
    this.pending = null
    this.publish({ ...this.state, value: merge(this.state.value) })
  }
  accept(value: T) {
    this.pending = null
    this.publish({ value, dirty: false, hydrated: true, revision: this.state.revision + 1 })
  }
}
