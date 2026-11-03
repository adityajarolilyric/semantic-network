import { DataPreview } from '../components/DataPreview'

export type PlanningTableStatus = {
  state: string
  rowCount: number | null
  builtAt?: string | null
  columns: {
    columnId: string
    name: string
    physicalType: string
    role?: string
    displayName?: string
    lineage?: { kind: string; dimensionId?: string; columnId?: string }
    ownerName?: string
  }[]
  rows: (string | null)[][]
  lastError?: { message: string } | null
}
export function PlanningResult({
  data,
  name = 'Planning-level preview',
  onRefresh = () => {},
  showRefresh = true,
}: {
  data: Pick<PlanningTableStatus, 'columns' | 'rows'> & { rowCount?: number | null }
  name?: string
  onRefresh?: () => void
  showRefresh?: boolean
}) {
  return (
    <DataPreview
      table={name}
      sampleOnly
      showRefresh={showRefresh}
      preview={{
        table: name,
        columns: data.columns.map((c) => ({ name: c.name, type: c.physicalType })),
        rows: data.rows.slice(0, 50),
        total: data.rowCount ?? data.rows.length,
        offset: 0,
        limit: 50,
      }}
      loading={false}
      error=""
      offset={0}
      limit={50}
      onOffset={() => {}}
      onLimit={() => {}}
      onRefresh={onRefresh}
    />
  )
}
