import { useEffect, useId, useRef, useState } from 'react'

export type FinalSql = { sql: string; dialect: string; warning?: string | null }

/** Loads SQL without executing the displayed statement. */
export function ViewSqlButton({
  loadSql,
  disabled = false,
  label = 'View SQL',
  heading = 'Final SQL',
  description = 'ClickHouse · Full query, before preview sampling',
}: {
  loadSql: (signal: AbortSignal) => Promise<FinalSql>
  disabled?: boolean
  label?: string
  heading?: string
  description?: string
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const request = useRef<AbortController | null>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const title = useId()
  const [open, setOpen] = useState(false)
  const [sql, setSql] = useState('')
  const [warning, setWarning] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [copyStatus, setCopyStatus] = useState('')
  useEffect(() => {
    if (open) dialog.current?.showModal()
  }, [open])
  useEffect(() => () => request.current?.abort(), [])
  function close() {
    request.current?.abort()
    dialog.current?.close()
    setOpen(false)
    trigger.current?.focus()
  }
  async function show() {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setSql('')
    setError('')
    setWarning('')
    setCopyStatus('')
    setLoading(true)
    setOpen(true)
    try {
      const result = await loadSql(controller.signal)
      if (!controller.signal.aborted) {
        setSql(result.sql)
        setWarning(result.warning || '')
      }
    } catch (error) {
      if (!controller.signal.aborted)
        setError(error instanceof Error ? error.message : 'Could not load SQL.')
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="secondary-button"
        disabled={disabled}
        onClick={() => void show()}
      >
        {label}
      </button>
      {open && (
        <dialog
          ref={dialog}
          className="sql-dialog"
          aria-labelledby={title}
          onCancel={(event) => {
            event.preventDefault()
            close()
          }}
        >
          <header className="sql-dialog-heading">
            <div>
              <h2 id={title}>{heading}</h2>
              <p>{description}</p>
            </div>
            <button type="button" className="secondary-button" onClick={close}>
              Close
            </button>
          </header>
          {loading && <p role="status">Loading SQL…</p>}
          {error && (
            <p role="alert" className="meta-error">
              {error}
            </p>
          )}
          {warning && <p role="status">{warning}</p>}
          {sql && (
            <pre tabIndex={0} aria-label="Final ClickHouse SQL">
              <code>{sql}</code>
            </pre>
          )}
          <footer className="sql-dialog-actions">
            <span role="status">{copyStatus}</span>
            <button
              type="button"
              className="primary-button"
              disabled={!sql || loading}
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(sql)
                  setCopyStatus('Copied')
                } catch {
                  setCopyStatus('Copy failed. Select the SQL text and copy it manually.')
                }
              }}
            >
              Copy SQL
            </button>
          </footer>
        </dialog>
      )}
    </>
  )
}
