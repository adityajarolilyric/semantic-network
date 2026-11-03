import { useEffect, useRef, useState, type ReactNode } from 'react'
import { DataPreview } from '../../components/DataPreview'
import { warehouseApi } from '../../lib/api'
import type { TablePreview } from '../../types'
import type { Source } from '../types'

/** Raw means the imported physical table, before every semantic transformation. */
export function RawDataPreview({
  source,
  disabled = false,
  renderActions,
}: {
  source?: Source
  disabled?: boolean
  renderActions?: (button: ReactNode) => ReactNode
}) {
  const [preview, setPreview] = useState<TablePreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const request = useRef<AbortController | null>(null)
  useEffect(() => {
    request.current?.abort()
    setOpen(false)
    setPreview(null)
    setError('')
    setLoading(false)
    return () => request.current?.abort()
  }, [source?.objectId])
  async function show() {
    if (!source) return
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setOpen(true)
    setLoading(true)
    setPreview(null)
    setError('')
    try {
      const result = await warehouseApi.preview(
        source.definition.tableName,
        0,
        50,
        controller.signal,
      )
      if (!controller.signal.aborted) setPreview(result)
    } catch (e) {
      if (!controller.signal.aborted)
        setError(e instanceof Error ? e.message : 'Raw preview failed.')
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }
  const rawButton = (
    <button
      type="button"
      className="secondary-button"
      disabled={disabled || !source || loading}
      onClick={() => void show()}
    >
      Raw Data Preview
    </button>
  )
  return (
    <div className={renderActions ? 'object-preview-footer' : 'meta-subsection'}>
      <div className={renderActions ? '' : 'meta-toolbar'}>
        {renderActions ? renderActions(rawButton) : rawButton}
        {open && (
          <button
            type="button"
            className="secondary-button"
            onClick={() => {
              request.current?.abort()
              setOpen(false)
              setLoading(false)
              setPreview(null)
            }}
          >
            Close raw preview
          </button>
        )}
      </div>
      {open && source && (
        <DataPreview
          table={source.name + ' — raw data'}
          preview={preview}
          loading={loading}
          error={error}
          sampleOnly
          showRefresh={false}
          offset={0}
          limit={50}
          onOffset={() => {}}
          onLimit={() => {}}
          onRefresh={() => void show()}
        />
      )}
    </div>
  )
}
