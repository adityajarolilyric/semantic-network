import { useState } from 'react'
import type { Source } from '../types'
import { Actions, Checks, Text, type EditorProps } from './Fields'
export function SourceEditor({ initial, onSave, onCancel, busy }: EditorProps<Source>) {
  const [value, set] = useState(initial)
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        void onSave(value)
      }}
    >
      <h3>{value.name}</h3>
      <p className="meta-hint">
        Physical table and column definitions are read-only. Key uniqueness is a declaration, not a
        verified warehouse check.
      </p>
      <div className="meta-schema">
        <table>
          <thead>
            <tr>
              <th>Physical name</th>
              <th>Type</th>
              <th>Nullable</th>
            </tr>
          </thead>
          <tbody>
            {value.definition.columns.map((c) => (
              <tr key={c.columnId}>
                <td>{c.name}</td>
                <td>{c.physicalType}</td>
                <td>{c.nullable ? 'Yes' : 'No'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Text
        label="Description"
        value={value.description}
        required={false}
        onChange={(description) => set({ ...value, description })}
      />
      <Checks
        label="Declared key columns"
        items={value.definition.columns.map((c) => ({
          value: c.columnId,
          label: c.name + (c.nullable ? ' (nullable)' : ''),
          disabled: c.nullable,
        }))}
        selected={value.definition.keyColumns}
        onChange={(keyColumns) =>
          set({ ...value, definition: { ...value.definition, keyColumns } })
        }
      />
      <Actions busy={busy} onCancel={onCancel} />
    </form>
  )
}
