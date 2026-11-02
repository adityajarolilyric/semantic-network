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
  data: Pick<PlanningTableStatus, 'columns' | 'rows'>
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
        rows: data.rows.slice(0, 100),
        total: data.rows.length,
        offset: 0,
        limit: 100,
      }}
      loading={false}
      error=""
      offset={0}
      limit={100}
      onOffset={() => {}}
      onLimit={() => {}}
      onRefresh={onRefresh}
    />
  )
}
