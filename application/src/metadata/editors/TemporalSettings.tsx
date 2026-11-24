import { useEffect, useState } from 'react'
import { inputOf, type CalcDefinition, type Dimension, type GrainOption } from '../types'
import { metadataApi } from '../api'
import { Checks, Select, Text } from './Fields'
import type { FormulaValidation } from '../useFormulaValidation'

export function TemporalSettings({
  value: supplied,
  resolution,
  level,
  dimension,
  base,
  onChange,
}: {
  value: CalcDefinition['temporal']
  resolution?: FormulaValidation['temporalResolution']
  level?: GrainOption
  dimension?: Dimension
  base?: string
  onChange: (value: CalcDefinition['temporal']) => void
}) {
  const [advanced, setAdvanced] = useState(false)
  useEffect(() => setAdvanced(false), [level?.objectId])
  const inferred = resolution?.context
  const value = supplied ??
    inferred ?? {
      timeKeyId: '',
      orderColumnId: '',
      periodUnit: 'week' as const,
      partitionColumnIds: [],
      minimumPeriods: 1,
    }
  const [schema, setSchema] = useState<{ columnId: string; name: string }[]>([])
  const [schemaError, setSchemaError] = useState('')
  const signature = dimension ? JSON.stringify(inputOf(dimension)) : ''
  useEffect(() => {
    setSchema([])
    setSchemaError('')
    if (!advanced || resolution?.columns.length || !base || !signature) return
    const controller = new AbortController()
    void metadataApi
      .request<{ columns: { columnId: string; name: string }[] }>(
        base + '/dimensions/schema',
        'POST',
        JSON.parse(signature),
        undefined,
        controller.signal,
      )
      .then((result) => {
        if (!controller.signal.aborted) setSchema(result.columns)
      })
      .catch((error) => {
        if (!controller.signal.aborted) setSchemaError(error.message)
      })
    return () => controller.abort()
  }, [base, signature, advanced, !!resolution?.columns.length])
  const keys =
    resolution?.keys.map((key) => ({ grainColumnId: key.columnId, name: key.name })) ??
    level?.definition.grain.columns ??
    []
  const columns = resolution?.columns.length
    ? resolution.columns.map((column) => ({ value: column.columnId, label: column.name }))
    : schema.length
      ? schema.map((column) => ({ value: column.columnId, label: column.name }))
      : [
          ...keys.map((column) => ({ value: column.grainColumnId, label: column.name })),
          ...(level?.definition.populationAttributes ?? []).map((column) => ({
            value: column.columnId,
            label: column.name,
          })),
        ]
  return (
    <fieldset className="meta-subpanel">
      <legend>Time series</legend>
      {resolution?.summary && <p>{resolution.summary}</p>}
      {resolution?.reason && !supplied && <p className="meta-hint">{resolution.reason}</p>}
      <details
        key={level?.objectId}
        open={advanced || (!!resolution && !(supplied || inferred))}
        onToggle={(event) => setAdvanced(event.currentTarget.open)}
      >
        <summary>Advanced time-series settings</summary>
        {supplied && (
          <button type="button" className="secondary-button" onClick={() => onChange(null)}>
            Use automatic settings
          </button>
        )}
        {schemaError && (
          <p className="meta-error" role="alert">
            {schemaError}
          </p>
        )}
        <div className="meta-grid">
          <Select
            label="Time key"
            value={value.timeKeyId}
            items={keys.map((column) => ({ value: column.grainColumnId, label: column.name }))}
            onChange={(timeKeyId) =>
              onChange({
                ...value,
                timeKeyId,
                partitionColumnIds: keys
                  .map((column) => column.grainColumnId)
                  .filter((key) => key !== timeKeyId && !resolution?.timeKeyIds.includes(key)),
              })
            }
          />
          <Select
            label="Calendar ordering column"
            value={value.orderColumnId}
            items={columns}
            onChange={(orderColumnId) => onChange({ ...value, orderColumnId })}
          />
          <Select
            label="Period unit"
            value={value.periodUnit}
            items={['day', 'week', 'month', 'index']}
            onChange={(periodUnit) =>
              onChange({ ...value, periodUnit: periodUnit as typeof value.periodUnit })
            }
          />
          <Select
            label="Reset by (optional)"
            value={value.resetColumnId ?? ''}
            required={false}
            items={columns}
            onChange={(resetColumnId) =>
              onChange({ ...value, resetColumnId: resetColumnId || null })
            }
          />
          <Text
            label="Minimum populated periods"
            value={String(value.minimumPeriods ?? 1)}
            onChange={(minimumPeriods) =>
              onChange({ ...value, minimumPeriods: Number(minimumPeriods) })
            }
          />
        </div>
        <Checks
          label="Series keys"
          items={keys
            .filter((column) => column.grainColumnId !== value.timeKeyId)
            .map((column) => ({ value: column.grainColumnId, label: column.name }))}
          selected={value.partitionColumnIds}
          onChange={(partitionColumnIds) => onChange({ ...value, partitionColumnIds })}
        />
        <p className="meta-hint">
          Each series is calculated independently in calendar order. Use a Date for day/week/month
          or a consecutive numeric calendar index for labels such as W01. Missing periods are not
          filled with zero.
        </p>
      </details>
    </fieldset>
  )
}
