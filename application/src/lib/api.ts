import type { CsvImportResult, TableCatalog, TablePreview, WarehouseStatus } from '../types'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
    readonly requestId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export class WarehouseApi {
  constructor(private readonly base = '/api/v1/warehouse') {}

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    let response: Response
    try {
      response = await fetch(this.base + path, options)
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') throw error
      throw new ApiError(
        'Cannot reach the source service. Check that the backend is running, then try again.',
        'network_error',
        0,
      )
    }
    let body: unknown
    try {
      body = await response.json()
    } catch {
      throw new ApiError(
        'The source service returned an unreadable response. Please try again.',
        'invalid_response',
        response.status,
      )
    }
    if (!response.ok) {
      const error = (body as { error?: { message?: string; code?: string; requestId?: string } })
        .error
      throw new ApiError(
        error?.message || 'The request could not be completed. Please try again.',
        error?.code || 'request_failed',
        response.status,
        error?.requestId,
      )
    }
    return body as T
  }

  status(signal?: AbortSignal) {
    return this.request<WarehouseStatus>('/status', { signal })
  }
  tables(signal?: AbortSignal) {
    return this.request<TableCatalog>('/tables', { signal })
  }
  preview(table: string, offset: number, limit: number, signal?: AbortSignal) {
    return this.request<TablePreview>(
      `/tables/${encodeURIComponent(table)}/preview?${new URLSearchParams({ offset: String(offset), limit: String(limit) })}`,
      { signal },
    )
  }
  upload(file: File, tableName: string, importId: string) {
    const body = new FormData()
    body.append('tableName', tableName)
    body.append('file', file)
    return this.request<CsvImportResult>('/imports/csv', {
      method: 'POST',
      body,
      headers: { 'Idempotency-Key': importId },
    })
  }
}

export const warehouseApi = new WarehouseApi()
