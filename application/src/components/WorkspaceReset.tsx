import { useState } from 'react'
import { metadataApi } from '../metadata/api'

export function WorkspaceReset() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return (
    <section className="workspace-reset" aria-label="Reset workspace">
      <div>
        <strong>Start over</strong>
        <p>Clear all source data and semantic definitions, including published revisions.</p>
      </div>
      <button
        type="button"
        className="workspace-reset-button"
        disabled={busy}
        onClick={async () => {
          if (
            !window.confirm(
              'Nuke everything? This permanently deletes ALL semantic models, revisions (including published versions), source data, and all tables/views in this application’s configured ClickHouse database. A fresh default semantic model and draft revision will remain. This cannot be undone.',
            )
          )
            return
          setBusy(true)
          setError('')
          try {
            await metadataApi.request('/workspace/reset', 'POST', { confirmed: true })
            const url = new URL(location.href)
            url.search = '?tab=sources'
            url.hash = ''
            location.replace(url.toString())
          } catch (e) {
            setError(
              e instanceof Error ? e.message : 'Reset failed. Retry Nuke everything to finish.',
            )
            setBusy(false)
          }
        }}
      >
        {busy ? 'Clearing workspace…' : 'Nuke everything'}
      </button>
      {busy && (
        <p role="status">
          Finishing active operations and clearing data. Please keep this page open.
        </p>
      )}
      {error && (
        <p className="meta-error" role="alert">
          {error} If reset was interrupted, retry Nuke everything.
        </p>
      )}
    </section>
  )
}
