type NumericRow = Record<string, number | null>

/** Index existing row positions; never merge equal x values or copy samples.
 * Connected sparse series may omit rows whose y value is absent. Dense rows
 * and custom prototypes keep the chart's ordinary scanning path.
 */
export function sparseChartIndex(
  rows: readonly NumericRow[], keys: readonly string[],
): ReadonlyMap<string, readonly number[]> | null {
  const indexes = new Map([...new Set(keys)].map((key) => [key, [] as number[]]))
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index]
    if (!row) return null
    const prototype = Object.getPrototypeOf(row)
    if (prototype !== Object.prototype && prototype !== null) return null
    let matchedKey: string | undefined
    for (const key of Object.getOwnPropertyNames(row)) {
      if (!indexes.has(key)) continue
      // Accessors are not immutable JSON samples; leave their reads uncached.
      const descriptor = Object.getOwnPropertyDescriptor(row, key)!
      if (!('value' in descriptor)) return null
      const value: unknown = descriptor.value
      if (typeof value !== 'number' || !Number.isFinite(value)) continue
      if (matchedKey !== undefined) return null
      matchedKey = key
    }
    if (matchedKey !== undefined) indexes.get(matchedKey)!.push(index)
  }
  return indexes
}
