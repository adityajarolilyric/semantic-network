import { Check, FileSpreadsheet, LoaderCircle, Upload, X } from 'lucide-react'
import { useRef, useState, type DragEvent } from 'react'
import { ApiError, warehouseApi } from '../lib/api'
import { formatBytes, suggestTableNames, validTableName } from '../lib/files'
import type { CsvImportResult } from '../types'

type Props = {
  maxRows: number
  maxBytes: number
  onImported: (result: CsvImportResult) => Promise<void>
}
type UploadItem = {
  id: string
  file: File
  tableName: string
  importId: string
  state: 'pending' | 'uploading' | 'succeeded' | 'failed'
  result?: CsvImportResult
  error?: string
  requestId?: string
  refreshWarning?: string
}
const maxFiles = 10
const importKey = () => `csv_${crypto.randomUUID().replaceAll('-', '')}`

export function UploadPanel({ maxRows, maxBytes, onImported }: Props) {
  const [items, setItems] = useState<UploadItem[]>([])
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const uploading = useRef(false)
  const [activeFile, setActiveFile] = useState('')
  const [completed, setCompleted] = useState(0)
  const [batchTotal, setBatchTotal] = useState(0)
  const pending = items.filter((item) => item.state !== 'succeeded')
  const succeeded = items.filter((item) => item.state === 'succeeded').length
  const update = (id: string, patch: Partial<UploadItem>) =>
    setItems((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item)))

  const selectFiles = (files: FileList | File[]) => {
    if (uploading.current) return
    setError('')
    if (!files.length || files.length > maxFiles) {
      setError(`Choose between 1 and ${maxFiles} CSV files at a time.`)
      return
    }
    const selected = Array.from(files)
    for (const file of selected) {
      if (!file.name.toLowerCase().endsWith('.csv')) {
        setError(`${file.name}: choose a .csv file.`)
        return
      }
      if (file.size > maxBytes) {
        setError(
          `${file.name}: this file is too large. Choose a CSV under ${formatBytes(maxBytes)}.`,
        )
        return
      }
    }
    const names = suggestTableNames(selected.map((file) => file.name))
    setItems(
      selected.map((file, index) => ({
        id: crypto.randomUUID(),
        file,
        tableName: names[index],
        importId: importKey(),
        state: 'pending',
      })),
    )
    setCompleted(0)
  }

  const drop = (event: DragEvent) => {
    event.preventDefault()
    setDragging(false)
    selectFiles(event.dataTransfer.files)
  }
  const upload = async () => {
    if (uploading.current || !pending.length) return
    if (pending.some((item) => !validTableName(item.tableName))) {
      setError(
        'Use letters, numbers and underscores for each table name. Start with a letter or underscore (maximum 128 characters).',
      )
      return
    }
    if (new Set(items.map((item) => item.tableName)).size !== items.length) {
      setError(
        'Each file needs a different table name. Rename the duplicate names before uploading.',
      )
      return
    }
    uploading.current = true
    setBusy(true)
    setError('')
    setCompleted(0)
    setBatchTotal(pending.length)
    try {
      for (const [index, item] of pending.entries()) {
        setActiveFile(item.file.name)
        setCompleted(index)
        update(item.id, {
          state: 'uploading',
          error: undefined,
          requestId: undefined,
          refreshWarning: undefined,
        })
        let result: CsvImportResult
        try {
          result = await warehouseApi.upload(item.file, item.tableName, item.importId)
        } catch (failure) {
          update(item.id, {
            state: 'failed',
            error:
              failure instanceof Error
                ? failure.message
                : 'Could not upload this CSV. Please try again.',
            requestId: failure instanceof ApiError ? failure.requestId : undefined,
          })
          continue
        }
        update(item.id, {
          state: result.semanticSetupComplete === false ? 'failed' : 'succeeded',
          result,
          error:
            result.semanticSetupComplete === false
              ? 'CSV stored. Retry to complete semantic setup without importing it again.'
              : undefined,
        })
        try {
          await onImported(result)
        } catch {
          update(item.id, {
            refreshWarning:
              'Table created, but the catalog could not refresh. Reload the source list to preview it.',
          })
        }
      }
    } finally {
      setActiveFile('')
      setBusy(false)
      uploading.current = false
    }
  }

  return (
    <section className="panel upload-panel" aria-labelledby="upload-title">
      <div className="panel-heading">
        <div className="heading-icon">
          <Upload size={18} aria-hidden="true" />
        </div>
        <div>
          <h2 id="upload-title">Upload CSV files</h2>
          <p>Create one source table per file, up to 10 files at a time.</p>
        </div>
      </div>
      <div
        className={`drop-area${dragging ? ' is-dragging' : ''}${busy ? ' is-busy' : ''}`}
        onDragOver={(event) => {
          event.preventDefault()
          if (!busy) setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={drop}
      >
        <div className="upload-symbol">
          <FileSpreadsheet size={28} strokeWidth={1.5} aria-hidden="true" />
        </div>
        <p className="drop-title">Drop up to 10 CSV files here</p>
        <p className="drop-hint">or choose files from your computer</p>
        <button
          type="button"
          className="button button-secondary"
          disabled={busy}
          onClick={() => input.current?.click()}
        >
          <Upload size={15} aria-hidden="true" />
          Choose files
        </button>
        <input
          ref={input}
          type="file"
          multiple
          accept=".csv,text/csv"
          aria-label="CSV files"
          className="sr-only"
          disabled={busy}
          onChange={(event) => {
            if (event.target.files?.length) selectFiles(event.target.files)
            event.target.value = ''
          }}
        />
        <span className="file-limit">
          Up to {maxRows.toLocaleString()} rows · {formatBytes(maxBytes)} per file
        </span>
      </div>
      {items.length > 0 && (
        <form
          className="upload-form"
          onSubmit={(event) => {
            event.preventDefault()
            void upload()
          }}
        >
          <div className="upload-queue" aria-label="CSV upload queue">
            {items.map((item) => (
              <div className="upload-queue-item" key={item.id}>
                <div className="chosen-file">
                  <FileSpreadsheet size={19} aria-hidden="true" />
                  <div>
                    <strong title={item.file.name}>{item.file.name}</strong>
                    <span>
                      {formatBytes(item.file.size)} ·{' '}
                      {item.state === 'succeeded'
                        ? 'Imported'
                        : item.state === 'uploading'
                          ? 'Uploading…'
                          : item.state === 'failed'
                            ? 'Failed'
                            : 'Queued'}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={
                      items.length === 1 ? 'Remove selected file' : `Remove ${item.file.name}`
                    }
                    disabled={busy}
                    onClick={() => {
                      setItems((current) => current.filter((value) => value.id !== item.id))
                      setError('')
                    }}
                  >
                    <X size={16} aria-hidden="true" />
                  </button>
                </div>
                {item.state !== 'succeeded' && (
                  <>
                    <label className="field-label" htmlFor={`table-${item.id}`}>
                      {items.length === 1 ? 'Table name' : `Table name for ${item.file.name}`}
                    </label>
                    <input
                      id={`table-${item.id}`}
                      className="text-input"
                      value={item.tableName}
                      maxLength={128}
                      disabled={busy}
                      autoComplete="off"
                      spellCheck={false}
                      aria-describedby="table-name-help"
                      onChange={(event) => {
                        update(item.id, {
                          tableName: event.target.value,
                          importId: importKey(),
                          state: 'pending',
                          error: undefined,
                          requestId: undefined,
                        })
                        setError('')
                      }}
                    />
                  </>
                )}
                {item.error && (
                  <div className="feedback feedback-error" role="alert">
                    <p>{item.error}</p>
                    {item.requestId && (
                      <span className="request-id">Reference: {item.requestId}</span>
                    )}
                  </div>
                )}
                {item.result && (
                  <div className="feedback feedback-success" role="status">
                    <Check size={17} aria-hidden="true" />
                    <div>
                      <strong>{item.result.table}</strong>
                      <p>
                        {item.result.rowCount.toLocaleString()}{' '}
                        {item.result.rowCount === 1 ? 'row' : 'rows'} · {item.result.columns.length}{' '}
                        {item.result.columns.length === 1 ? 'column' : 'columns'} · ready to preview
                      </p>
                      {item.result.warnings.map((warning) => (
                        <p key={warning}>{warning}</p>
                      ))}
                      {item.refreshWarning && <p>{item.refreshWarning}</p>}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
          {pending.length > 0 && (
            <>
              <p id="table-name-help" className="field-help">
                Use a new name for each table. Existing tables are preserved.
              </p>
              <button
                type="submit"
                className="button button-primary upload-submit"
                disabled={busy || pending.some((item) => !item.tableName)}
              >
                <span>
                  {busy ? (
                    <LoaderCircle size={16} className="spin" aria-hidden="true" />
                  ) : (
                    <Upload size={16} aria-hidden="true" />
                  )}
                </span>
                {busy
                  ? items.length === 1
                    ? 'Creating table…'
                    : 'Uploading files…'
                  : items.length === 1 && items[0].result?.semanticSetupComplete !== false
                    ? 'Upload & create table'
                    : items.some((item) => item.state === 'failed') || succeeded > 0
                      ? `Retry ${pending.length} ${pending.length === 1 ? 'file' : 'files'}`
                      : `Upload & create ${pending.length} tables`}
              </button>
            </>
          )}
          {!busy && items.length > 1 && succeeded > 0 && (
            <p className="status-message" role="status">
              {succeeded} of {items.length} tables created.
              {pending.length > 0 ? ' Correct or retry the remaining files.' : ''}
            </p>
          )}
        </form>
      )}
      {busy && (
        <p className="status-message" role="status">
          Uploading {completed + 1} of {batchTotal}: {activeFile}. Please keep this page open.
        </p>
      )}
      {error && (
        <div className="feedback feedback-error" role="alert">
          <p>{error}</p>
        </div>
      )}
      <div className="upload-notes">
        <h3>Ready for modeling</h3>
        <p>
          Column headers stay as written. Types are detected from your data, and empty cells are
          stored as null.
        </p>
      </div>
    </section>
  )
}
