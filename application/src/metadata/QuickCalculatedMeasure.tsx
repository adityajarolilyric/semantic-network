import { useEffect, useRef, useState } from 'react'
import { MeasureEditor } from './editors/MeasureEditor'
import { identity, type Measure, type Snapshot } from './types'

export function QuickCalculatedMeasure({
  data,
  base,
  baseLevelId,
  busy,
  onSave,
  onClose,
}: {
  data: Snapshot
  base: string
  baseLevelId: string
  busy: boolean
  onSave: (measure: Measure) => Promise<void>
  onClose: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [error, setError] = useState('')
  const [initial] = useState<Measure>(() => ({
    ...identity('measure'),
    kind: 'measure',
    displayName: '',
    business: { unit: null, aliases: [] },
    definition: {
      measureType: 'calculated',
      nativePlanningLevelId: baseLevelId,
      calculationType: 'AggregateFirst',
      resultType: { type: 'number', nullable: true },
      editable: false,
      formula: { displayText: '', expression: { type: 'literal', value: 0 } },
    },
  }))
  useEffect(() => {
    const element = dialog.current!
    const previous = document.activeElement
    element.showModal()
    element.querySelector<HTMLInputElement>('input')?.focus()
    return () => {
      element.close()
      if (previous instanceof HTMLElement) previous.focus()
    }
  }, [])
  return (
    <dialog
      ref={dialog}
      className="quick-measure-modal"
      aria-labelledby="quick-measure-title"
      onCancel={(event) => {
        event.preventDefault()
        if (!busy) onClose()
      }}
    >
      <h2 id="quick-measure-title">Create Measure</h2>
      <p className="meta-hint">
        {data.planningLevels.find((l) => l.objectId === baseLevelId)?.name} · AggregateFirst
      </p>
      {error && (
        <p role="alert" className="meta-error">
          {error}
        </p>
      )}
      <fieldset disabled={busy} className="quick-measure-fields">
        <MeasureEditor
          quick
          initial={initial}
          data={data}
          editing={false}
          busy={busy}
          base={base}
          baseLevelId={baseLevelId}
          onCancel={onClose}
          onSave={async (measure) => {
            setError('')
            try {
              await onSave(measure)
            } catch (error) {
              setError(error instanceof Error ? error.message : 'Could not create measure.')
            }
          }}
        />
      </fieldset>
    </dialog>
  )
}
