import { FieldHelp } from '../../components/FieldHelp'
import { cloneElement, useId, type ReactElement } from 'react'
import type { Identity, ObjectInput, Snapshot } from '../types'
export type EditorProps<T extends ObjectInput> = {
  initial: T
  data: Snapshot
  editing: boolean
  onSave: (input: T) => Promise<void>
  onCancel: () => void
  busy: boolean
}
export const options = (items: { objectId: string; name: string }[]) =>
  items.map((x) => ({ value: x.objectId, label: x.name }))
export function Field({
  label,
  children,
  hint,
}: {
  label: string
  children: ReactElement<{ id?: string; 'aria-describedby'?: string }>
  hint?: string
}) {
  const id = useId()
  return (
    <div className="meta-field">
      <div className="field-label-with-help">
        <label htmlFor={id}>{label}</label>
        <FieldHelp label={label} />
      </div>
      {cloneElement(children, { id, 'aria-describedby': hint ? id + '-hint' : undefined })}
      {hint && <small id={id + '-hint'}>{hint}</small>}
    </div>
  )
}
export function Text({
  label,
  value,
  onChange,
  required = true,
  disabled = false,
  hint,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  required?: boolean
  disabled?: boolean
  hint?: string
}) {
  return (
    <Field label={label} hint={hint}>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required={required}
        disabled={disabled}
        maxLength={label.toLowerCase().includes('description') ? 4000 : 200}
      />
    </Field>
  )
}
export function Select({
  label,
  value,
  onChange,
  items,
  required = true,
  disabled = false,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  items: (string | { value: string; label: string })[]
  disabled?: boolean
  required?: boolean
}) {
  return (
    <Field label={label}>
      <select
        disabled={disabled}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required={required}
      >
        <option value="">Select…</option>
        {items.map((i) =>
          typeof i === 'string' ? (
            <option key={i}>{i}</option>
          ) : (
            <option key={i.value} value={i.value}>
              {i.label}
            </option>
          ),
        )}
      </select>
    </Field>
  )
}
export function Check({
  label,
  value,
  onChange,
  disabled = false,
  help = true,
}: {
  label: string
  value: boolean
  help?: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
}) {
  return (
    <div className="field-check-with-help">
      <label className="meta-check">
        <input
          type="checkbox"
          checked={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
        />
        {label}
      </label>
      {help && <FieldHelp label={label} />}
    </div>
  )
}
export function Checks({
  label,
  items,
  selected,
  onChange,
  bulk = false,
  disabled = false,
}: {
  label: string
  items: { value: string; label: string; disabled?: boolean }[]
  selected: string[]
  onChange: (v: string[]) => void
  bulk?: boolean
  disabled?: boolean
}) {
  const id = useId()
  return (
    <fieldset className="meta-checks" aria-label={label} disabled={disabled}>
      <legend>
        {label}
        <FieldHelp label={label} />
      </legend>
      {bulk && items.length > 0 && (
        <div className="meta-bulk-actions">
          <button
            type="button"
            onClick={() =>
              onChange([
                ...new Set([...selected, ...items.filter((i) => !i.disabled).map((i) => i.value)]),
              ])
            }
          >
            Select all
          </button>
          <span aria-hidden="true">·</span>
          <button
            type="button"
            onClick={() =>
              onChange(selected.filter((id) => items.find((i) => i.value === id)?.disabled))
            }
          >
            Deselect all
          </button>
        </div>
      )}
      {items.length === 0 && <small>No columns available.</small>}
      {items.map((i) => (
        <Check
          key={id + i.value}
          label={i.label}
          help={false}
          disabled={i.disabled}
          value={selected.includes(i.value)}
          onChange={(v) =>
            onChange(v ? [...selected, i.value] : selected.filter((k) => k !== i.value))
          }
        />
      ))}
    </fieldset>
  )
}
export function IdentityFields({
  value,
  onChange,
  semantic = true,
}: {
  value: Identity & { displayName?: string }
  onChange: (v: Identity & { displayName?: string }) => void
  editing: boolean
  semantic?: boolean
}) {
  return (
    <div className="meta-grid">
      <Text
        label="Name"
        value={value.name}
        onChange={(v) =>
          onChange({
            ...value,
            name: v,
            ...(semantic ? { displayName: v } : {}),
          })
        }
      />
      <Text
        label="Description"
        value={value.description}
        required={false}
        onChange={(v) => onChange({ ...value, description: v })}
      />
    </div>
  )
}
export function Actions({
  busy,
  onCancel,
  canSave = true,
}: {
  busy: boolean
  onCancel: () => void
  canSave?: boolean
}) {
  return (
    <div className="meta-actions">
      <button type="button" className="secondary-button" onClick={onCancel} disabled={busy}>
        Cancel
      </button>
      <button className="primary-button" disabled={busy || !canSave}>
        {busy ? 'Saving…' : 'Save metadata'}
      </button>
    </div>
  )
}
