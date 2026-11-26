import { FieldHelp } from './FieldHelp'
import { ArrowUpRight, Database, LoaderCircle, RefreshCw, Search, Table2 } from 'lucide-react'
import { useState } from 'react'
import type { WarehouseTable } from '../types'

export type TableManagement = {
  onImported?: () => void
  pending?: Record<string, boolean>
  managed: Set<string>
  disabled: boolean
  onChange: (table: string, managed: boolean) => void
}

type Props = {
  management?: TableManagement
  tables: WarehouseTable[]
  selected: string | null
  loading: boolean
  error: string
  truncated: boolean
  onSelect: (name: string) => void
  onRefresh: () => void
}

export function TableCatalog({
  tables,
  management,
  selected,
  loading,
  error,
  truncated,
  onSelect,
  onRefresh,
}: Props) {
  const [query, setQuery] = useState('')
  const filtered = tables.filter((table) =>
    table.name.toLowerCase().includes(query.trim().toLowerCase()),
  )
  return (
    <section className="panel catalog-panel" aria-labelledby="catalog-title">
      <div className="catalog-header">
        <div>
          <h2 id="catalog-title">
            Source tables <span className="count-badge">{tables.length}</span>
          </h2>
          <p>Tables available in your connected warehouse.</p>
        </div>
        <button
          className="icon-button"
          type="button"
          onClick={onRefresh}
          disabled={loading}
          aria-label="Refresh source tables"
        >
          <RefreshCw size={17} className={loading ? 'spin' : ''} aria-hidden="true" />
        </button>
      </div>
      <div className="search-field">
        <Search size={16} aria-hidden="true" />
        <label className="sr-only" htmlFor="table-search">
          Search tables
        </label>
        <input
          id="table-search"
          placeholder="Search tables…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      {error && (
        <div className="catalog-error" role="alert">
          <p>{error}</p>
          <button type="button" className="text-button" onClick={onRefresh} disabled={loading}>
            Try again
          </button>
        </div>
      )}
      {loading && tables.length === 0 && (
        <div className="empty-state" role="status">
          <LoaderCircle className="spin" size={24} aria-hidden="true" />
          <h3>Loading your sources</h3>
          <p>Checking the warehouse for available tables.</p>
        </div>
      )}
      {!loading && !error && tables.length === 0 && (
        <div className="empty-state">
          <div className="empty-symbol">
            <Database size={28} strokeWidth={1.5} aria-hidden="true" />
          </div>
          <h3>Your first source starts here</h3>
          <p>
            Upload a CSV to create a table.
            <br />
            Select it here to explore the data.
          </p>
        </div>
      )}
      {tables.length > 0 && (
        <div className="catalog-table-wrap">
          <table className="catalog-table">
            <thead>
              <tr>
                <th>Table name</th>
                <th className="numeric">Rows</th>
                <th className="numeric">Columns</th>
                <th className="managed-column" aria-label="Managed">
                  Managed
                  <FieldHelp label="Managed" />
                </th>
                <th>
                  <span className="sr-only">Preview</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((table) => (
                <tr key={table.name} className={selected === table.name ? 'is-selected' : ''}>
                  <td>
                    <button
                      type="button"
                      className="table-name-button"
                      onClick={() => onSelect(table.name)}
                      aria-pressed={selected === table.name}
                    >
                      <Table2 size={17} aria-hidden="true" />
                      <span title={table.name}>{table.name}</span>
                    </button>
                    <span className="table-engine">{table.engine}</span>
                  </td>
                  <td className="numeric">{table.rowCount?.toLocaleString() ?? '—'}</td>
                  <td className="numeric">{table.columnCount.toLocaleString()}</td>
                  <td className="managed-column">
                    <input
                      type="checkbox"
                      aria-label={`Manage ${table.name}`}
                      checked={
                        management?.pending?.[table.name] ??
                        management?.managed.has(table.name) ??
                        false
                      }
                      disabled={
                        !management ||
                        management.disabled ||
                        management.pending?.[table.name] !== undefined
                      }
                      onChange={(e) => management?.onChange(table.name, e.target.checked)}
                    />
                  </td>
                  <td>
                    <button
                      className="icon-button preview-action"
                      type="button"
                      aria-label={`Preview ${table.name}`}
                      onClick={() => onSelect(table.name)}
                    >
                      <ArrowUpRight size={17} aria-hidden="true" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length === 0 && <p className="no-results">No tables match “{query}”.</p>}
        </div>
      )}
      {truncated && (
        <p className="catalog-note">Showing the first 1,000 tables in the warehouse.</p>
      )}
      <div className="catalog-footer">
        <span className="small-dot" aria-hidden="true" />
        Select a table to preview its rows and column types.
      </div>
    </section>
  )
}
