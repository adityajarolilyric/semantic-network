/** Browser-only layout preferences. Never cache query results or edit contexts. */
export type ViewRef = {
  memberCatalogId?: string
  dimensionId?: string
  transactionId?: string
  columnId: string
}
export const viewReference = ({
  memberCatalogId,
  dimensionId,
  transactionId,
  columnId,
}: ViewRef): ViewRef =>
  memberCatalogId
    ? { memberCatalogId, columnId }
    : transactionId
      ? { transactionId, columnId }
      : { dimensionId, columnId }
export type ViewPreferences = {
  buildId: string | null
  rows: ViewRef[]
  columns: ViewRef[]
  measures: string[]
}
const memory = new Map<string, string>()
const key = (scope: string) => 'semantic:decision-view:v1:' + scope
const priorKey = (scope: string) => 'semantic:' + ['planning', 'view'].join('-') + ':v1:' + scope
export function clearViewPreferences(scope: string) {
  memory.delete(key(scope))
  try {
    window.localStorage.removeItem(key(scope))
    window.localStorage.removeItem(priorKey(scope))
  } catch {
    /* Memory-only mode. */
  }
}
export function saveViewPreferences(scope: string, value: ViewPreferences) {
  if (!value.rows.length && !value.columns.length && !value.measures.length) {
    clearViewPreferences(scope)
    return
  }
  const refs = (items: ViewRef[]) => items.map(viewReference)
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
export function readViewPreferences(
  scope: string,
  _buildId: string | null,
): ViewPreferences | null {
  let raw: string | null | undefined
  try {
    raw = window.localStorage.getItem(key(scope))
    if (!raw) {
      const prior = window.localStorage.getItem(priorKey(scope))
      if (prior) {
        const value = JSON.parse(prior)
        const migrate = (refs: Record<string, string>[]) =>
          refs.map((ref) => {
            const native = ref['planning' + 'LevelId']
            return native
              ? { dimensionId: native, columnId: ref.columnId }
              : { memberCatalogId: ref.dimensionId, columnId: ref.columnId }
          })
        raw = JSON.stringify({
          ...value,
          rows: migrate(value.rows ?? []),
          columns: migrate(value.columns ?? []),
        })
        window.localStorage.setItem(key(scope), raw)
      }
    }
  } catch {
    raw = memory.get(key(scope))
  }
  if (!raw) return null
  try {
    if (raw.length > 20000) throw new Error('Oversized preferences')
    const value = JSON.parse(raw) as ViewPreferences
    const refs = (items: unknown): items is ViewRef[] =>
      Array.isArray(items) &&
      items.every(
        (v) =>
          v &&
          typeof v.columnId === 'string' &&
          [v.memberCatalogId, v.dimensionId, v.transactionId].filter((id) => typeof id === 'string')
            .length === 1,
      )
    if (
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
