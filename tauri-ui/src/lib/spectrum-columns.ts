export type SpectrumColumns = { wavelengthNm: number[]; intensity: number[]; gridRevision?: number }

// Wire shape: grid-cached frames omit the wavelength axis and instead carry
// the grid revision the backend used; the frontend merges the axis from its
// cached copy when the revision matches.
export type SpectrumColumnsPatch = {
  wavelengthNm?: number[]
  intensity: number[]
  gridRevision?: number
}

export function decodeColumnarSpectrum(value: unknown): SpectrumColumnsPatch {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("后端光谱必须使用列式数组协议")
  }
  const columns = value as Record<string, unknown>
  const keys = Object.keys(columns)
  if (!keys.every((key) => key === "wavelengthNm" || key === "intensity" || key === "gridRevision")) {
    throw new Error("后端光谱只能包含 wavelengthNm、intensity 和 gridRevision 字段")
  }
  if (
    !Array.isArray(columns.intensity)
    || !columns.intensity.every((item) => typeof item === "number" && Number.isFinite(item))
  ) {
    throw new Error("后端光谱缺少有效的 intensity 数组")
  }
  if (columns.wavelengthNm !== undefined) {
    if (
      !Array.isArray(columns.wavelengthNm)
      || !columns.wavelengthNm.every((item) => typeof item === "number" && Number.isFinite(item))
    ) {
      throw new Error("后端光谱缺少有效的 wavelengthNm 数组")
    }
    if (columns.wavelengthNm.length !== columns.intensity.length) {
      throw new Error("后端光谱两轴长度不一致")
    }
  } else if (
    typeof columns.gridRevision !== "number"
    || !Number.isFinite(columns.gridRevision)
  ) {
    throw new Error("省略 wavelengthNm 时必须携带 gridRevision")
  }
  if (
    columns.gridRevision !== undefined
    && (typeof columns.gridRevision !== "number" || !Number.isFinite(columns.gridRevision))
  ) {
    throw new Error("gridRevision 必须是有限数值")
  }
  return columns as unknown as SpectrumColumnsPatch
}

export function mergeSpectrumColumns(
  previous: SpectrumColumns | undefined,
  patch: SpectrumColumnsPatch | undefined,
): SpectrumColumns {
  if (!patch) return previous ?? { wavelengthNm: [], intensity: [] }
  if (patch.wavelengthNm !== undefined) {
    return { wavelengthNm: patch.wavelengthNm, intensity: patch.intensity, gridRevision: patch.gridRevision }
  }
  const gridMatches = previous !== undefined
    && previous.gridRevision !== undefined
    && previous.gridRevision === patch.gridRevision
  return {
    wavelengthNm: gridMatches ? previous.wavelengthNm : [],
    intensity: patch.intensity,
    gridRevision: patch.gridRevision,
  }
}
