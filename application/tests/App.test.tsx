import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../src/App'
import { warehouseApi } from '../src/lib/api'
import type { TablePreview, WarehouseTable } from '../src/types'

const column = { name: 'product_id', type: 'Nullable(String)' }
const table = (name: string, rows = 1): WarehouseTable => ({
  name,
  engine: 'MergeTree',
  rowCount: rows,
  columnCount: 1,
})
let tables: WarehouseTable[]
let connected: boolean
let maxBytes: number
let uploadError: { status: number; message: string; code: string } | null
let calls: { url: string; init?: RequestInit }[]

function preview(name: string, offset = 0, limit = 50): TablePreview {
  const total = tables.find((item) => item.name === name)?.rowCount ?? 1
  return {
    table: name,
    columns: [column],
    rows: Array.from({ length: Math.min(limit, Math.max(0, total - offset)) }, (_, index) => [
      String(offset + index + 1).padStart(3, '0'),
    ]),
    total,
    offset,
    limit,
  }
}

beforeEach(() => {
  tables = []
  connected = true
  maxBytes = 10_485_760
  uploadError = null
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url.endsWith('/workspace/bootstrap')) return Response.json({})
      if (url.startsWith('/api/v1/models')) return Response.json({ items: [], nextCursor: null })
      if (url.endsWith('/status'))
        return Response.json({
          connected,
          database: connected ? 'semantic' : null,
          maxCsvRows: 5000,
          maxFileBytes: maxBytes,
          message: connected
            ? null
            : 'ClickHouse is unavailable. Check the server connection and retry.',
        })
      if (url.endsWith('/tables')) return Response.json({ tables, truncated: false })
      if (url.includes('/preview?')) {
        const parsed = new URL(url, 'http://localhost')
        const name = decodeURIComponent(parsed.pathname.split('/')[5])
        return Response.json(
          preview(
            name,
            Number(parsed.searchParams.get('offset')),
            Number(parsed.searchParams.get('limit')),
          ),
        )
      }
      if (url.endsWith('/imports/csv')) {
        if (uploadError)
          return Response.json(
            { error: { ...uploadError, requestId: 'request_test' } },
            { status: uploadError.status },
          )
        const name = (init?.body as FormData).get('tableName') as string
        tables = [...tables.filter((item) => item.name !== name), table(name)]
        return Response.json(
          {
            table: name,
            rowCount: 1,
            columns: [column],
            replayed: false,
            warnings: [],
            status: 'created',
          },
          { status: 201 },
        )
      }
      throw new Error(`Unexpected request: ${url}`)
    }),
  )
})

describe('source workspace', () => {
  it('loads the empty warehouse and shows a useful starting state', async () => {
    render(<App />)
    expect(await screen.findByText('Your first source starts here')).toBeVisible()
    expect(screen.getByText('ClickHouse connected')).toBeVisible()
    expect(screen.getByText('Up to 5,000 rows · 10.0 MB per file')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Choose files' })).toBeEnabled()
  })

  it('uploads a file as FormData, refreshes the catalog and opens its preview', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('Your first source starts here')
    await user.upload(
      screen.getByLabelText('CSV files'),
      new File(['product_id\n001\n'], 'Forecast.csv', { type: 'text/csv' }),
    )
    expect(screen.getByLabelText('Table name')).toHaveValue('Forecast')
    await user.click(screen.getByRole('button', { name: 'Upload & create table' }))
    expect(await screen.findByText(/ready to preview/)).toBeVisible()
    expect(await screen.findByRole('region', { name: 'Forecast rows' })).toBeVisible()
    expect(screen.getByText('001')).toBeVisible()
    const upload = calls.find((call) => call.url.endsWith('/imports/csv'))!
    expect(upload.init?.body).toBeInstanceOf(FormData)
    expect((upload.init?.body as FormData).get('tableName')).toBe('Forecast')
    expect((upload.init?.headers as Record<string, string>)['Idempotency-Key']).toMatch(/^csv_/)
    expect((upload.init?.headers as Record<string, string>)['Content-Type']).toBeUndefined()
  })

  it('keeps the file available after a table-name conflict', async () => {
    const user = userEvent.setup()
    uploadError = {
      status: 409,
      code: 'conflict',
      message: "A table named 'Forecast' already exists. Choose another table name.",
    }
    render(<App />)
    await screen.findByText('Your first source starts here')
    await user.upload(
      screen.getByLabelText('CSV files'),
      new File(['x\n1'], 'Forecast.csv', { type: 'text/csv' }),
    )
    await user.click(screen.getByRole('button', { name: 'Upload & create table' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('already exists')
    expect(screen.getByText('Forecast.csv')).toBeVisible()
    const firstKey = (
      calls.find((call) => call.url.endsWith('/imports/csv'))!.init?.headers as Record<
        string,
        string
      >
    )['Idempotency-Key']
    await user.clear(screen.getByLabelText('Table name'))
    await user.type(screen.getByLabelText('Table name'), 'Forecast_new')
    uploadError = null
    await user.click(screen.getByRole('button', { name: 'Upload & create table' }))
    expect(await screen.findByText(/ready to preview/)).toBeVisible()
    const imports = calls.filter((call) => call.url.endsWith('/imports/csv'))
    expect((imports[1].init?.headers as Record<string, string>)['Idempotency-Key']).not.toBe(
      firstKey,
    )
  })

  it('preserves the idempotency key when retrying the unchanged upload', async () => {
    const user = userEvent.setup()
    uploadError = {
      status: 503,
      code: 'warehouse_unavailable',
      message: 'Temporary failure. Retry the same upload.',
    }
    render(<App />)
    await screen.findByText('Your first source starts here')
    await user.upload(
      screen.getByLabelText('CSV files'),
      new File(['x\n1'], 'Forecast.csv', { type: 'text/csv' }),
    )
    await user.click(screen.getByRole('button', { name: 'Upload & create table' }))
    await screen.findByRole('alert')
    uploadError = null
    await user.click(screen.getByRole('button', { name: 'Upload & create table' }))
    await screen.findByText(/ready to preview/)
    const imports = calls.filter((call) => call.url.endsWith('/imports/csv'))
    expect((imports[0].init?.headers as Record<string, string>)['Idempotency-Key']).toBe(
      (imports[1].init?.headers as Record<string, string>)['Idempotency-Key'],
    )
  })

  it('shows server validation errors for files over 5000 rows', async () => {
    const user = userEvent.setup()
    uploadError = {
      status: 422,
      code: 'row_limit',
      message: 'CSV files may contain at most 5,000 data rows, excluding the header.',
    }
    render(<App />)
    await screen.findByText('Your first source starts here')
    await user.upload(
      screen.getByLabelText('CSV files'),
      new File(['x\n' + '1\n'.repeat(5001)], 'TooLarge.csv', { type: 'text/csv' }),
    )
    await user.click(screen.getByRole('button', { name: 'Upload & create table' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('at most 5,000')
    expect(screen.getByRole('button', { name: 'Upload & create table' })).toBeEnabled()
  })

  it('blocks oversized files locally and validates table names', async () => {
    const user = userEvent.setup()
    maxBytes = 1024
    render(<App />)
    await screen.findByText('Your first source starts here')
    await user.upload(
      screen.getByLabelText('CSV files'),
      new File(['x'.repeat(2048)], 'Big.csv', { type: 'text/csv' }),
    )
    expect(screen.getByRole('alert')).toHaveTextContent('too large')
    await user.upload(
      screen.getByLabelText('CSV files'),
      new File(['x\n1'], 'Small.csv', { type: 'text/csv' }),
    )
    await user.clear(screen.getByLabelText('Table name'))
    await user.type(screen.getByLabelText('Table name'), 'bad;name')
    await user.click(screen.getByRole('button', { name: 'Upload & create table' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Use letters')
    expect(calls.filter((call) => call.url.endsWith('/imports/csv'))).toHaveLength(0)
  })

  it('processes only one upload at a time', async () => {
    const user = userEvent.setup()
    let finish!: (value: Awaited<ReturnType<typeof warehouseApi.upload>>) => void
    const request = vi.spyOn(warehouseApi, 'upload').mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    render(<App />)
    await screen.findByText('Your first source starts here')
    await user.upload(
      screen.getByLabelText('CSV files'),
      new File(['x\n1'], 'Forecast.csv', { type: 'text/csv' }),
    )
    await user.click(screen.getByRole('button', { name: 'Upload & create table' }))
    expect(screen.getByRole('button', { name: 'Creating table…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Choose files' })).toBeDisabled()
    expect(request).toHaveBeenCalledTimes(1)
    await act(async () => {
      finish({
        table: 'Forecast',
        columns: [column],
        rowCount: 1,
        replayed: false,
        warnings: [],
        status: 'created',
      })
    })
  })

  it('paginates previews and filters the source catalog', async () => {
    const user = userEvent.setup()
    tables = [table('Forecast', 60), table('Sales')]
    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Preview Forecast' }))
    expect(await screen.findByText('Showing 1–50 of 60 rows')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Next page' }))
    expect(await screen.findByText('Showing 51–60 of 60 rows')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
    await user.type(screen.getByLabelText('Search tables'), 'Sales')
    expect(screen.queryByRole('button', { name: 'Preview Forecast' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Preview Sales' })).toBeVisible()
  })

  it('ignores a stale preview after the user selects another table', async () => {
    const user = userEvent.setup()
    tables = [table('Forecast'), table('Sales')]
    let finish!: (value: TablePreview) => void
    vi.spyOn(warehouseApi, 'preview').mockImplementation((name) =>
      name === 'Forecast'
        ? new Promise((resolve) => {
            finish = resolve
          })
        : Promise.resolve(preview('Sales')),
    )
    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Preview Forecast' }))
    await user.click(screen.getByRole('button', { name: 'Preview Sales' }))
    expect(await screen.findByRole('region', { name: 'Sales rows' })).toBeVisible()
    await act(async () => {
      finish(preview('Forecast'))
    })
    expect(screen.getByRole('region', { name: 'Sales rows' })).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Forecast rows' })).not.toBeInTheDocument()
  })

  it('supports retrying an unavailable warehouse', async () => {
    const user = userEvent.setup()
    connected = false
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('ClickHouse is unavailable')
    connected = true
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Your first source starts here')).toBeVisible()
    expect(screen.getByText('ClickHouse connected')).toBeVisible()
  })

  it('accepts dropping multiple files together', async () => {
    render(<App />)
    await screen.findByText('Your first source starts here')
    fireEvent.drop(screen.getByText('Drop up to 10 CSV files here').parentElement!, {
      dataTransfer: { files: [new File(['x'], 'a.csv'), new File(['y'], 'b.csv')] },
    })
    expect(screen.getByLabelText('Table name for a.csv')).toHaveValue('a')
    expect(screen.getByLabelText('Table name for b.csv')).toHaveValue('b')
    await waitFor(() =>
      expect(calls.filter((call) => call.url.endsWith('/imports/csv'))).toHaveLength(0),
    )
  })
})
