import { useCallback, useEffect, useRef, useState } from 'react'
import { warehouseApi } from '../lib/api'
import type { CsvImportResult, TablePreview, WarehouseStatus, WarehouseTable } from '../types'

export function useSources() {
  const [status, setStatus] = useState<WarehouseStatus | null>(null)
  const [tables, setTables] = useState<WarehouseTable[]>([])
  const [truncated, setTruncated] = useState(false)
  const [refreshing, setRefreshing] = useState(true)
  const [catalogError, setCatalogError] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [limit, setLimit] = useState(50)
  const [preview, setPreview] = useState<TablePreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const [previewVersion, setPreviewVersion] = useState(0)
  const catalogRequest = useRef<AbortController | null>(null)
  const previewRequest = useRef(0)

  const refresh = useCallback(async (preferredTable?: string) => {
    catalogRequest.current?.abort()
    const controller = new AbortController()
    catalogRequest.current = controller
    setRefreshing(true)
    setCatalogError('')
    try {
      const nextStatus = await warehouseApi.status(controller.signal)
      if (controller.signal.aborted) return
      setStatus(nextStatus)
      if (!nextStatus.connected) {
        setCatalogError(nextStatus.message || 'ClickHouse is unavailable.')
        return
      }
      const catalog = await warehouseApi.tables(controller.signal)
      if (controller.signal.aborted) return
      setTables(catalog.tables)
      setTruncated(catalog.truncated)
      setSelected(
        (current) =>
          preferredTable ||
          (catalog.tables.some((table) => table.name === current) ? current : null),
      )
      if (preferredTable) setOffset(0)
      setPreviewVersion((current) => current + 1)
    } catch (error) {
      if (!controller.signal.aborted) {
        setCatalogError(error instanceof Error ? error.message : 'Could not load source tables.')
        setStatus((current) => (current ? { ...current, connected: false } : null))
      }
    } finally {
      if (!controller.signal.aborted) setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
    return () => catalogRequest.current?.abort()
  }, [refresh])

  useEffect(() => {
    const requestId = ++previewRequest.current
    const controller = new AbortController()
    setPreview(null)
    setPreviewError('')
    if (!selected) {
      setPreviewLoading(false)
      return () => controller.abort()
    }
    setPreviewLoading(true)
    void warehouseApi
      .preview(selected, offset, limit, controller.signal)
      .then((result) => {
        if (requestId !== previewRequest.current || controller.signal.aborted) return
        if (result.total > 0 && offset >= result.total) {
          setOffset(Math.floor((result.total - 1) / limit) * limit)
          return
        }
        setPreview(result)
      })
      .catch((error) => {
        if (!controller.signal.aborted && requestId === previewRequest.current)
          setPreviewError(error instanceof Error ? error.message : 'Could not preview this table.')
      })
      .finally(() => {
        if (!controller.signal.aborted && requestId === previewRequest.current)
          setPreviewLoading(false)
      })
    return () => controller.abort()
  }, [selected, offset, limit, previewVersion])

  const select = useCallback((table: string) => {
    setSelected(table)
    setOffset(0)
  }, [])
  const changeLimit = useCallback((value: number) => {
    setLimit(Math.min(value, 50))
    setOffset(0)
  }, [])
  const refreshPreview = useCallback(() => setPreviewVersion((current) => current + 1), [])
  const imported = useCallback(
    async (result: CsvImportResult) => {
      setTables((current) =>
        [
          ...current.filter((table) => table.name !== result.table),
          {
            name: result.table,
            engine: 'MergeTree',
            rowCount: result.rowCount,
            columnCount: result.columns.length,
          },
        ].sort((a, b) => a.name.localeCompare(b.name)),
      )
      setSelected(result.table)
      setOffset(0)
      await refresh(result.table)
    },
    [refresh],
  )

  return {
    status,
    tables,
    truncated,
    refreshing,
    catalogError,
    selected,
    select,
    refresh,
    imported,
    offset,
    setOffset,
    limit,
    changeLimit,
    preview,
    previewLoading,
    previewError,
    refreshPreview,
  }
}
