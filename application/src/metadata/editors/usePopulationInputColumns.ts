import { useEffect, useState } from 'react'
import { metadataApi } from '../api'
import type { Population, SourceColumn } from '../types'

/** Reuse Transaction input schema APIs for the self-contained population pipeline. */
export function usePopulationInputColumns(
  population: Population,
  base?: string,
  sourceColumns: SourceColumn[] = [],
) {
  const config = population.operation === 'source' ? population.inputTransform : null
  const sourceId = population.operation === 'source' ? population.sourceId : ''
  const signature = JSON.stringify([base, config ?? null, sourceColumns])
  const [result, setResult] = useState<{
    signature: string
    sourceId: string
    columns: SourceColumn[]
  } | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    setResult(null)
    setError('')
    if (!base || !config || !sourceId) return
    const controller = new AbortController()
    const calendar = !!config.calendarResolution
    void metadataApi
      .request<{ columns: SourceColumn[] }>(
        base + (calendar ? '/transactions/calendar/schema' : '/transactions/input/schema'),
        'POST',
        calendar
          ? {
              transactionId: config.objectId,
              sourceId,
              pivot: config.pivot,
              calendarResolution: config.calendarResolution,
            }
          : { objectId: config.objectId, sourceId, pivot: config.pivot },
        undefined,
        controller.signal,
      )
      .then((value) => {
        if (!controller.signal.aborted) setResult({ signature, sourceId, columns: value.columns })
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message)
      })
    return () => controller.abort()
  }, [base, sourceId, signature])
  return {
    columns: result?.signature === signature && result?.sourceId === sourceId ? result.columns : [],
    error,
    configured: !!config,
  }
}
