export type WarehouseColumn = { name: string; type: string }
export type WarehouseTable = {
  name: string
  engine: string
  rowCount: number | null
  columnCount: number
}
export type WarehouseStatus = {
  connected: boolean
  database: string | null
  maxCsvRows: number
  maxFileBytes: number
  message: string | null
}
export type TableCatalog = { tables: WarehouseTable[]; truncated: boolean }
export type TablePreview = {
  table: string
  columns: WarehouseColumn[]
  rows: (string | null)[][]
  total: number
  offset: number
  limit: number
}
export type CsvImportResult = {
  table: string
  rowCount: number
  columns: WarehouseColumn[]
  replayed: boolean
  warnings: string[]
  status: 'created'
}
