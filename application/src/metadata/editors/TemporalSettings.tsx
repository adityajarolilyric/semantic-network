import { useEffect, useState } from 'react'
import { inputOf, type CalcDefinition, type Dimension, type GrainOption } from '../types'
import { metadataApi } from '../api'
import { Checks, Select, Text } from './Fields'

export function TemporalSettings({
  value,
  level,
  dimension,
  base,
  onChange,
}: {
  value: NonNullable<CalcDefinition['temporal']>
  level?: GrainOption
  dimension?: Dimension
  base?: string
  onChange: (value: NonNullable<CalcDefinition['temporal']>) => void
}) {
  const [schema, setSchema] = useState<{ columnId: string; name: string }[]>([])
  const [schemaError, setSchemaError] = useState('')
  const signature = dimension ? JSON.stringify(inputOf(dimension)) : ''
  useEffect(() => {
    setSchema([])
    setSchemaError('')
    if (!base || !signature) return
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
  }, [base, signature])
  const keys = level?.definition.grain.columns ?? []
  const columns = schema.length
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
                .filter((key) => key !== timeKeyId),
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
          onChange={(resetColumnId) => onChange({ ...value, resetColumnId: resetColumnId || null })}
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
        Each series is calculated independently in calendar order. Use a Date for day/week/month or
        a consecutive numeric calendar index for labels such as W01. Missing periods are not filled
        with zero.
      </p>
    </fieldset>
  )
}
