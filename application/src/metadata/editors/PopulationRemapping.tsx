import type { Dimension, GrainColumn } from '../types'
import { Select } from './Fields'

type Reference = GrainColumn['populationRef']
export type PopulationMappingOption = { label: string; reference: Reference; nullable?: boolean }
const referenceKey = (ref: Reference) =>
  JSON.stringify([
    ref.factorId ?? null,
    ref.columnId ?? null,
    ref.sourceGrainColumnId ?? null,
    ref.sourceOutputColumnId ?? null,
  ])

/** Explicitly remap a changed population without replacing stable semantic field IDs. */
export function PopulationRemapping({
  value,
  options,
  onChange,
}: {
  value: Dimension
  options: PopulationMappingOption[]
  onChange: (definition: Dimension['definition']) => void
}) {
  const definition = value.definition
  function mapField(id: string, attribute: boolean, key: string) {
    const selected = options.find((option) => referenceKey(option.reference) === key)
    if (!selected) return
    const reference =
      attribute && selected.reference.sourceGrainColumnId
        ? { sourceOutputColumnId: selected.reference.sourceGrainColumnId }
        : selected.reference
    const grain = {
      ...definition.grain,
      columns: definition.grain.columns.map((column) =>
        !attribute && column.grainColumnId === id
          ? { ...column, populationRef: reference }
          : column,
      ),
    }
    const attributes = (definition.populationAttributes ?? []).map((column) =>
      attribute && column.columnId === id ? { ...column, populationRef: reference } : column,
    )
    const population =
      definition.population.operation === 'cartesian_product'
        ? {
            ...definition.population,
            factors: definition.population.factors.map((factor) => ({
              ...factor,
              selectedColumnIds: [...grain.columns, ...attributes]
                .filter((column) => column.populationRef.factorId === factor.factorId)
                .map((column) => column.populationRef.columnId!)
                .filter(Boolean),
            })),
          }
        : definition.population
    onChange({ ...definition, grain, population, populationAttributes: attributes })
  }
  return (
    <div className="meta-subsection">
      <h3>Map existing fields to the new population</h3>
      <p className="meta-hint">
        Map each field explicitly. Existing keys and relationships are preserved.
      </p>
      <div className="meta-grid">
        {[
          ...definition.grain.columns.map((column) => ({
            id: column.grainColumnId,
            name: column.name,
            ref: column.populationRef,
            attribute: false,
          })),
          ...(definition.populationAttributes ?? []).map((column) => ({
            id: column.columnId,
            name: column.name,
            ref: column.populationRef,
            attribute: true,
          })),
        ].map((column) => {
          const choices = options.filter((option) => column.attribute || !option.nullable)
          const selected = choices.find(
            (option) =>
              referenceKey(option.reference) === referenceKey(column.ref) ||
              (column.attribute &&
                !!column.ref.sourceOutputColumnId &&
                option.reference.sourceGrainColumnId === column.ref.sourceOutputColumnId),
          )
          return (
            <Select
              key={column.id}
              label={
                'Population column for ' + (column.attribute ? 'attribute ' : '') + column.name
              }
              value={selected ? referenceKey(selected.reference) : ''}
              items={choices.map((option) => ({
                value: referenceKey(option.reference),
                label: option.label,
              }))}
              onChange={(key) => mapField(column.id, column.attribute, key)}
            />
          )
        })}
      </div>
    </div>
  )
}
