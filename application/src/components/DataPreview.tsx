import { useId } from 'react'
import { formatNumericDisplay } from '../lib/numberFormat'
import {
  ArrowLeft,
  ArrowRight,
  CalendarDays,
  Eye,
  Hash,
  LoaderCircle,
  RefreshCw,
  ToggleLeft,
  Type,
} from 'lucide-react'
import type { TablePreview } from '../types'

type Props = {
  showRefresh?: boolean
  sampleOnly?: boolean
  table: string | null
  preview: TablePreview | null
  loading: boolean
  error: string
  offset: number
  limit: number
  onOffset: (offset: number) => void
  onLimit: (limit: number) => void
  onRefresh: () => void
}

function ColumnType({ type }: { type: string }) {
  const Icon = /Int|Float|Decimal/.test(type)
    ? Hash
    : /Date/.test(type)
      ? CalendarDays
      : /Bool/.test(type)
        ? ToggleLeft
        : Type
  return (
    <span className="column-type">
      <Icon size={12} aria-hidden="true" />
      {type}
    </span>
  )
}

export function DataPreview({
  table,
  sampleOnly = false,
  showRefresh = true,
  preview,
  loading,
  error,
  offset,
  limit,
  onOffset,
  onLimit,
  onRefresh,
}: Props) {
  const titleId = useId()
  if (!table)
    return (
      <section className="panel preview-placeholder">
        <Eye size={23} strokeWidth={1.5} aria-hidden="true" />
        <div>
          <h2>Take a closer look</h2>
          <p>Select a source table above to preview its data.</p>
        </div>
      </section>
    )
  const total = preview?.total ?? 0
  return (
    <section className="panel data-preview" aria-labelledby={titleId} aria-busy={loading}>
      <div className="preview-heading">
        <div>
          <span className="eyebrow">Data preview</span>
          <h2 id={titleId}>{table}</h2>
        </div>
        <div className="preview-actions">
          {preview && <span className="preview-total">{total.toLocaleString()} total rows</span>}
          {showRefresh && (
            <button
              className="icon-button"
              type="button"
              aria-label="Refresh preview"
              onClick={onRefresh}
              disabled={loading}
            >
              <RefreshCw size={17} className={loading ? 'spin' : ''} aria-hidden="true" />
            </button>
          )}
        </div>
      </div>
      {loading && (
        <div className="preview-loading" role="status">
          <LoaderCircle size={22} className="spin" aria-hidden="true" />
          Loading table data…
        </div>
      )}
      {error && (
        <div className="preview-error" role="alert">
          <p>{error}</p>
          <button type="button" className="button button-secondary" onClick={onRefresh}>
            Retry preview
          </button>
        </div>
      )}
      {preview && (
        <>
          <div className="data-scroll" tabIndex={0} role="region" aria-label={`${table} rows`}>
            <table className="data-table">
              <thead>
                <tr>
                  <th className="row-number" scope="col">
                    <span className="sr-only">Row</span>#
                  </th>
                  {preview.columns.map((column) => (
                    <th key={column.name} scope="col">
                      <span className="column-name">{column.name}</span>
                      <ColumnType type={column.type} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.rows.slice(0, 50).map((row, rowIndex) => (
                  <tr key={offset + rowIndex}>
                    <td className="row-number">{offset + rowIndex + 1}</td>
                    {row.map((value, index) => (
                      <td key={preview.columns[index].name}>
                        {value === null ? (
                          <span className="null-value">null</span>
                        ) : value === '' ? (
                          <span className="empty-value">empty</span>
                        ) : (
                          <span className="cell-value" title={value}>
                            {/Float|Decimal/.test(preview.columns[index].type)
                              ? formatNumericDisplay(value)
                              : value}
                          </span>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {total === 0 && (
            <p className="empty-preview">This table has column headers and no data rows.</p>
          )}
          <div className="preview-footer">
            <p aria-live="polite">
              {Math.min(preview.rows.length, 50).toLocaleString()}/{total.toLocaleString()} rows
            </p>
            {!sampleOnly && (
              <div className="pagination">
                <label htmlFor="rows-per-page">Rows per page</label>
                <select
                  id="rows-per-page"
                  value={limit}
                  onChange={(event) => onLimit(Number(event.target.value))}
                >
                  <option value={25}>25</option>
                  <option value={50}>50</option>
                </select>
                <button
                  type="button"
                  className="icon-button"
                  aria-label="Previous page"
                  disabled={offset === 0 || loading}
                  onClick={() => onOffset(Math.max(0, offset - limit))}
                >
                  <ArrowLeft size={16} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className="icon-button"
                  aria-label="Next page"
                  disabled={offset + preview.rows.length >= total || loading}
                  onClick={() => onOffset(offset + limit)}
                >
                  <ArrowRight size={16} aria-hidden="true" />
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </section>
  )
}
