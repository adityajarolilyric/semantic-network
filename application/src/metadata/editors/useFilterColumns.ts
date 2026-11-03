import { useEffect, useState } from 'react'
import { metadataApi } from '../api'
import type { FilterColumn } from './FilterEditor'

/** Schema analysis only; editing a predicate never triggers a build or another schema request. */
export function useFilterColumns(path: string | undefined, payload: unknown, enabled: boolean) {
  const signature = JSON.stringify(payload)
  const [state, setState] = useState<{ columns: FilterColumn[]; loading: boolean; error: string }>({
    columns: [],
    loading: false,
    error: '',
  })
  useEffect(() => {
    if (!enabled || !path) return
    const controller = new AbortController()
    setState({ columns: [], loading: true, error: '' })
    const timer = setTimeout(() => {
      void metadataApi
        .request<{ columns: FilterColumn[] }>(
          path,
          'POST',
          JSON.parse(signature),
          undefined,
          controller.signal,
        )
        .then((data) => {
          if (!controller.signal.aborted)
            setState({ columns: data.columns ?? [], loading: false, error: '' })
        })
        .catch((error) => {
          if (!controller.signal.aborted)
            setState({
              columns: [],
              loading: false,
              error:
                'Complete the preceding configuration to load filter columns. ' + error.message,
            })
        })
    }, 200)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [path, signature, enabled])
  return state
}
