import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { PlanningLevelEditor } from '../src/metadata/editors/PlanningLevelEditor'
import { projectionBindings } from '../src/metadata/projectionBindings'
import {
  emptySnapshot,
  type Binding,
  type Dimension,
  type GrainColumn,
  type PlanningLevel,
} from '../src/metadata/types'

const dim = (objectId: string, keys: string[]): Dimension => ({
  objectId,
  name: objectId,
  displayName: objectId,
  description: '',
  kind: 'dimension',
  isTimeDim: false,
  definition: {
    sourceId: 'source',
    columns: [...keys, 'label'].map((id) => ({
      columnId: id,
      sourceColumnId: id,
      name: id,
      displayName: id,
    })),
    keyColumnIds: keys,
    labelColumnId: 'label',
    attributeColumnIds: [...keys, 'label'],
    time: null,
  },
})
const dimensions = [
  dim('Product', ['product']),
  dim('Location', ['location']),
  dim('Time', ['date']),
  dim('Composite', ['product', 'location']),
]
const binding = (dimensionId: string, keys: string[]): Binding => ({
  dimensionId,
  keyMappings: keys.map((key) => ({ dimensionColumnId: key, grainColumnId: 'parent_' + key })),
  selectedAttributeColumnIds: ['label'],
})
const parent: PlanningLevel = {
  objectId: 'plt',
  kind: 'planningLevel',
  name: 'PLT',
  displayName: 'PLT',
  description: '',
  definition: {
    population: { operation: 'source', sourceId: 'source' },
    grain: {
      keyIds: ['parent_product', 'parent_location', 'parent_date'],
      columns: ['product', 'location', 'date'].map((key) => ({
        grainColumnId: 'parent_' + key,
        name: key,
        populationRef: { columnId: key },
      })),
    },
    dimensionBindings: [
      binding('Product', ['product']),
      binding('Location', ['location']),
      binding('Time', ['date']),
      binding('Composite', ['product', 'location']),
    ],
  },
}
const projected = (...keys: string[]): GrainColumn[] =>
  keys.map((key) => ({
    grainColumnId: 'child_' + key,
    name: key,
    populationRef: { sourceGrainColumnId: 'parent_' + key },
  }))
it('inherits by stable lineage, remaps child keys, and excludes incomplete composite keys', () => {
  const result = projectionBindings(parent, projected('product', 'date'), [], dimensions)
  expect(result.map((b) => b.dimensionId)).toEqual(['Product', 'Time'])
  expect(result[0].keyMappings).toEqual([
    { dimensionColumnId: 'product', grainColumnId: 'child_product' },
  ])
  expect(
    projectionBindings(parent, projected('product', 'location'), [], dimensions).map(
      (b) => b.dimensionId,
    ),
  ).toEqual(['Product', 'Location', 'Composite'])
  expect(parent.definition.dimensionBindings[0].keyMappings[0].grainColumnId).toBe('parent_product')
})
it('preserves deselected attributes on retained dimensions and drops bindings whose keys are removed', () => {
  const current = projectionBindings(parent, projected('product'), [], dimensions)
  current[0].selectedAttributeColumnIds = []
  const result = projectionBindings(parent, projected('product', 'date'), current, dimensions)
  expect(result[0].selectedAttributeColumnIds).toEqual([])
  expect(result[1].selectedAttributeColumnIds).toEqual(['label'])
  expect(
    projectionBindings(parent, projected('date'), result, dimensions).map((b) => b.dimensionId),
  ).toEqual(['Time'])
  expect(projectionBindings(undefined, projected('date'), result, dimensions)).toEqual([])
})
it('populates projection bindings interactively, supports bulk attributes and offers no grain renaming', async () => {
  const data = emptySnapshot()
  const envelope = { modelId: 'm', revisionId: 'r', version: 1, createdAt: '', updatedAt: '' }
  data.dimensions = dimensions.map((d) => ({ ...d, ...envelope }))
  data.planningLevels = [{ ...parent, ...envelope }]
  const initial: PlanningLevel = {
    objectId: 'pt',
    kind: 'planningLevel',
    name: 'PT',
    displayName: 'PT',
    description: '',
    definition: {
      population: { operation: 'distinct_projection', sourcePlanningLevelId: 'plt' },
      grain: { columns: [], keyIds: [] },
      dimensionBindings: [],
    },
  }
  const save = vi.fn(async () => {})
  render(
    <PlanningLevelEditor
      initial={initial}
      data={data}
      editing={false}
      busy={false}
      onSave={save}
      onCancel={() => {}}
    />,
  )
  const user = userEvent.setup()
  const keys = within(screen.getByRole('group', { name: 'Retain parent keys' }))
  await user.click(keys.getByLabelText('product'))
  expect(screen.getByRole('tab', { name: 'Product' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.queryByRole('tab', { name: 'Composite' })).not.toBeInTheDocument()
  let attributes = within(screen.getByRole('group', { name: 'Attributes from Product' }))
  await user.click(attributes.getByRole('button', { name: 'Deselect all' }))
  expect(attributes.getByLabelText('label')).not.toBeChecked()
  await user.click(keys.getByLabelText('date'))
  expect(screen.getByRole('tab', { name: 'Time' })).toBeInTheDocument()
  expect(
    within(screen.getByLabelText('Product dimension column 1'))
      .getAllByRole('option')
      .map((o) => o.textContent),
  ).toEqual(['Select…', 'product', 'label'])
  await user.click(screen.getByRole('tab', { name: 'Time' }))
  expect(
    within(screen.getByLabelText('Time dimension column 1'))
      .getAllByRole('option')
      .map((o) => o.textContent),
  ).toEqual(['Select…', 'date', 'label'])
  await user.click(screen.getByRole('tab', { name: 'Product' }))
  attributes = within(screen.getByRole('group', { name: 'Attributes from Product' }))
  expect(attributes.getByLabelText('label')).not.toBeChecked()
  await user.click(attributes.getByRole('button', { name: 'Select all' }))
  expect(attributes.getByLabelText('product')).toBeChecked()
  expect(attributes.getByLabelText('label')).toBeChecked()
  expect(screen.queryByRole('heading', { name: 'Grain identity' })).not.toBeInTheDocument()
  expect(screen.queryByLabelText(/Grain name/)).not.toBeInTheDocument()
  await user.click(keys.getByLabelText('date'))
  expect(screen.queryByRole('tab', { name: 'Time' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Save' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      definition: expect.objectContaining({
        dimensionBindings: [
          expect.objectContaining({
            dimensionId: 'Product',
            selectedAttributeColumnIds: ['product', 'label'],
          }),
        ],
      }),
    }),
  )
})

it('offers every column of the selected dimension and saves a non-primary attribute mapping', async () => {
  const data = emptySnapshot()
  const envelope = { modelId: 'm', revisionId: 'r', version: 1, createdAt: '', updatedAt: '' }
  data.dimensions = dimensions.map((d) => ({ ...d, ...envelope }))
  data.sources = [
    {
      ...envelope,
      objectId: 'source',
      kind: 'source',
      name: 'Source',
      description: '',
      definition: {
        tableName: 'Source',
        inScope: true,
        keyColumns: [],
        columns: ['product', 'location', 'date'].map((columnId) => ({
          columnId,
          name: columnId,
          physicalType: 'String',
          nullable: false,
        })),
      },
    },
  ]
  const initial: PlanningLevel = {
    ...parent,
    definition: {
      ...parent.definition,
      dimensionBindings: [
        {
          ...binding('Product', ['product']),
          keyMappings: [{ dimensionColumnId: 'product', grainColumnId: 'parent_location' }],
        },
      ],
    },
  }
  const save = vi.fn(async () => {})
  render(
    <PlanningLevelEditor
      initial={initial}
      data={data}
      editing={true}
      busy={false}
      onSave={save}
      onCancel={() => {}}
    />,
  )
  const select = screen.getByLabelText('Product dimension column 1')
  expect(select).toHaveValue('product')
  expect(
    within(select)
      .getAllByRole('option')
      .map((o) => o.textContent),
  ).toEqual(['Select…', 'product', 'label'])
  const user = userEvent.setup()
  await user.selectOptions(select, 'label')
  await user.selectOptions(
    screen.getByLabelText('Product planning-level column 1'),
    'parent_product',
  )
  expect(screen.queryByLabelText('Location dimension column 1')).not.toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText('Add dimension'), 'Location')
  expect(screen.getByRole('tab', { name: 'Location' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.queryByLabelText('Product dimension column 1')).not.toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: 'Product' }))
  expect(screen.getByLabelText('Product dimension column 1')).toHaveValue('label')
  await user.click(screen.getByRole('tab', { name: 'Location' }))
  await user.click(screen.getByRole('button', { name: 'Remove Location binding' }))
  expect(screen.queryByRole('tab', { name: 'Location' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Save' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      definition: expect.objectContaining({
        dimensionBindings: [
          expect.objectContaining({
            keyMappings: [{ dimensionColumnId: 'label', grainColumnId: 'parent_product' }],
          }),
        ],
      }),
    }),
  )
})

it('inherits a parent binding that joins a non-primary dimension attribute', () => {
  const alternateParent: PlanningLevel = {
    ...parent,
    definition: {
      ...parent.definition,
      dimensionBindings: [
        {
          dimensionId: 'Product',
          keyMappings: [{ dimensionColumnId: 'label', grainColumnId: 'parent_product' }],
          selectedAttributeColumnIds: [],
        },
      ],
    },
  }
  expect(
    projectionBindings(alternateParent, projected('product'), [], dimensions)[0].keyMappings,
  ).toEqual([{ dimensionColumnId: 'label', grainColumnId: 'child_product' }])
  expect(projectionBindings(alternateParent, projected('date'), [], dimensions)).toEqual([])
})

it('preserves manual attribute-level bindings while parent keys are not retained', () => {
  const columns: GrainColumn[] = [
    {
      grainColumnId: 'brand',
      name: 'Brand',
      populationRef: { sourceOutputColumnId: 'brand_output' },
    },
  ]
  const manual: Binding = {
    dimensionId: 'Product',
    keyMappings: [{ grainColumnId: 'brand', dimensionColumnId: 'label' }],
    selectedAttributeColumnIds: [],
  }
  expect(projectionBindings(parent, columns, [manual], dimensions)).toEqual([manual])
})
