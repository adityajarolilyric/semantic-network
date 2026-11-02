import { useCallback, useEffect, useState } from 'react'
import { metadataApi } from './api'
import { emptySnapshot, type Model, type Revision, type Snapshot } from './types'
export function useMetadata() {
  const params = new URLSearchParams(location.search)
  const [selection, setSelection] = useState({
    modelId: params.get('model') ?? '',
    revisionId: params.get('revision') ?? '',
  })
  const [view, setView] = useState<{
    models: Model[]
    revisions: Revision[]
    model?: Model
    revision?: Revision
    data: Snapshot
  }>({ models: [], revisions: [], data: emptySnapshot() })
  const [tick, setTick] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const refresh = useCallback(() => setTick((t) => t + 1), [])
  const choose = useCallback((modelId: string, revisionId = '') => {
    setLoading(true)
    setError('')
    setSelection({ modelId, revisionId })
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setLoading(true)
    setError('')
    void (async () => {
      try {
        let models = await metadataApi.models(controller.signal)
        if (models.length === 0) {
          await metadataApi.request(
            '/workspace/bootstrap',
            'POST',
            undefined,
            undefined,
            controller.signal,
          )
          models = await metadataApi.models(controller.signal)
        }
        const model = models.find((m) => m.modelId === selection.modelId) ?? models[0]
        const revisions = model ? await metadataApi.revisions(model.modelId, controller.signal) : []
        const revision =
          revisions.find((r) => r.revisionId === selection.revisionId) ??
          revisions.find((r) => r.revisionId === model?.draftRevisionId) ??
          revisions.find((r) => r.revisionId === model?.publishedRevisionId) ??
          revisions[0]
        const data =
          model && revision
            ? await metadataApi.snapshot(model.modelId, revision.revisionId, controller.signal)
            : emptySnapshot()
        if (active) {
          setView({ models, revisions, model, revision, data })
          const url = new URL(location.href)
          if (model) url.searchParams.set('model', model.modelId)
          else url.searchParams.delete('model')
          if (revision) url.searchParams.set('revision', revision.revisionId)
          else url.searchParams.delete('revision')
          history.replaceState(null, '', url)
        }
      } catch (error) {
        if (active) setError(error instanceof Error ? error.message : 'Could not load metadata.')
      } finally {
        if (active) setLoading(false)
      }
    })()
    return () => {
      active = false
      controller.abort()
    }
  }, [selection, tick])
  const updateRevisionVersion = useCallback(
    (modelId: string, revisionId: string, version: number) =>
      setView((current) => ({
        ...current,
        revision:
          current.revision?.modelId === modelId && current.revision.revisionId === revisionId
            ? { ...current.revision, version: Math.max(current.revision.version, version) }
            : current.revision,
        revisions: current.revisions.map((r) =>
          r.modelId === modelId && r.revisionId === revisionId
            ? { ...r, version: Math.max(r.version, version) }
            : r,
        ),
      })),
    [],
  )
  return { ...view, loading, error, refresh, choose, updateRevisionVersion }
}
