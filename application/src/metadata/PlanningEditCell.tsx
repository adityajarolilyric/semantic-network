import { useState } from 'react'

export function PlanningEditCell({
  value,
  label,
  disabled,
  onSave,
}: {
  value: string | null
  label: string
  disabled: boolean
  onSave: (value: number | null) => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  async function save() {
    if (disabled) return
    const next = text.trim() === '' ? null : Number(text)
    if (next !== null && !Number.isFinite(next)) {
      setError('Enter a finite number.')
      return
    }
    setError('')
    try {
      await onSave(next)
      setEditing(false)
    } catch (error) {
      setError(error instanceof Error ? error.message : 'The edit failed.')
    }
  }
  if (!editing)
    return (
      <button
        type="button"
        className="planning-cell-edit"
        disabled={disabled}
        aria-label={'Edit ' + label}
        onClick={() => {
          setText(value ?? '')
          setError('')
          setEditing(true)
        }}
      >
        {value === null ? '—' : value}
        <span aria-hidden="true"> ✎</span>
      </button>
    )
  return (
    <div className="planning-cell-editor">
      <input
        aria-label={'New value for ' + label}
        autoFocus
        type="text"
        inputMode="decimal"
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            void save()
          }
          if (e.key === 'Escape' && !disabled) setEditing(false)
        }}
      />
      <div>
        <button type="button" disabled={disabled} onClick={() => void save()}>
          Save
        </button>
        <button type="button" disabled={disabled} onClick={() => setEditing(false)}>
          Cancel
        </button>
      </div>
      {error && (
        <span role="alert" className="meta-error">
          {error}
        </span>
      )}
    </div>
  )
}
