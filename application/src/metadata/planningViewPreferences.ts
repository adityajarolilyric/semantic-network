/** Browser-only layout preferences. Never cache query results or edit contexts. */
export type ViewRef = { dimensionId: string; columnId: string }
export type ViewPreferences = {
  buildId: string | null
  rows: ViewRef[]
  columns: ViewRef[]
  measures: string[]
}
const memory = new Map<string, string>()
const key = (scope: string) => 'semantic:planning-view:v1:' + scope
export function clearViewPreferences(scope: string) {
  memory.delete(key(scope))
  try {
    window.localStorage.removeItem(key(scope))
  } catch {
    /* Memory-only mode. */
  }
}
export function saveViewPreferences(scope: string, value: ViewPreferences) {
  if (!value.rows.length && !value.columns.length && !value.measures.length) {
    clearViewPreferences(scope)
    return
  }
  const refs = (items: ViewRef[]) =>
    items.map(({ dimensionId, columnId }) => ({ dimensionId, columnId }))
  const serialized = JSON.stringify({
    buildId: value.buildId,
    rows: refs(value.rows),
    columns: refs(value.columns),
    measures: value.measures,
  })
  memory.set(key(scope), serialized)
  try {
    window.localStorage.setItem(key(scope), serialized)
  } catch {
    /* Keep navigation working when storage is blocked. */
  }
}
export function readViewPreferences(scope: string, buildId: string | null): ViewPreferences | null {
  let raw: string | null | undefined
  try {
    raw = window.localStorage.getItem(key(scope))
  } catch {
    raw = memory.get(key(scope))
  }
  if (!raw) return null
  try {
    if (raw.length > 20000) throw new Error('Oversized preferences')
    const value = JSON.parse(raw) as ViewPreferences
    const refs = (items: unknown): items is ViewRef[] =>
      Array.isArray(items) &&
      items.every((v) => v && typeof v.dimensionId === 'string' && typeof v.columnId === 'string')
    if (
      value.buildId !== buildId ||
      !refs(value.rows) ||
      !refs(value.columns) ||
      value.rows.length + value.columns.length > 8 ||
      !Array.isArray(value.measures) ||
      value.measures.length > 12 ||
      !value.measures.every((id) => typeof id === 'string')
    )
      throw new Error('Invalid preferences')
    return value
  } catch {
    clearViewPreferences(scope)
    return null
  }
}
