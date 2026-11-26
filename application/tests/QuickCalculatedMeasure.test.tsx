import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { QuickCalculatedMeasure } from '../src/metadata/QuickCalculatedMeasure'
import { metadataApi } from '../src/metadata/api'
import { emptySnapshot } from '../src/metadata/types'

beforeEach(() => {
  vi.spyOn(metadataApi, 'request').mockImplementation(async (path, _method, body) => {
    if (path.endsWith('/formula/language')) return { functions: [] } as never
    if (path.includes('/formula/attributes') || path.includes('/formula/measures'))
      return [] as never
    const text = (body as { displayText: string }).displayText
    return {
      name: text.startsWith('Invalid') ? 'Invalid' : 'Double',
      expressionText: text.includes('[FQTY]') ? '[FQTY]*2' : '1+2',
      valid: !text.endsWith('('),
      containsCounts: false,
      expression: { type: 'literal', value: 1 },
      resultType: { type: 'number', nullable: true },
      diagnostics: text.endsWith('(') ? [{ message: 'Incomplete function.' }] : [],
    } as never
  })
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '')
  }
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open')
  }
})
function setup() {
  const data = emptySnapshot()
  const envelope = {
    modelId: 'm',
    revisionId: 'r',
    version: 1,
    createdAt: '',
    updatedAt: '',
    description: '',
    displayName: '',
  }
  data.dimensions = ['base', 'week'].map((id) => ({
    ...envelope,
    kind: 'dimension',
    objectId: id,
    name: id,
    definition: {
      population: { operation: 'source', sourceId: 's' },
      grain: { keyIds: [], columns: [] },
      memberBindings: [],
    },
  }))
  data.transactions = ['base', 'week'].map((id) => ({
    ...envelope,
    kind: 'transaction',
    objectId: 'tx-' + id,
    name: id,
    definition: {
      sourceId: 's',
      dimensionId: id,
      mappingMode: 'direct',
      keyMappings: [],
      expectedRowsPerGrain: 'many',
      unmatchedGrainPolicy: 'reject',
    },
  }))
  data.measures = [
    {
      ...envelope,
      kind: 'measure',
      objectId: 'fqty',
      name: 'FQTY',
      business: { unit: null, aliases: [] },
      definition: {
        measureType: 'calculated',
        nativeGrainId: 'base',
        calculationType: 'AggregateFirst',
        editable: false,
        resultType: { type: 'number', nullable: true },
        formula: { displayText: '1', expression: { type: 'literal', value: 1 } },
      },
    },
  ]
  const save = vi.fn().mockResolvedValue(undefined)
  const close = vi.fn()
  render(
    <QuickCalculatedMeasure
      data={data}
      base="/models/m/revisions/r"
      baseLevelId="base"
      busy={false}
      onSave={save}
      onClose={close}
    />,
  )
  return { user: userEvent.setup(), save, close }
}
it('creates a Base AggregateFirst measure with a compact formula editor', async () => {
  const { user, save } = setup()
  expect(screen.queryByLabelText('Name')).not.toBeInTheDocument()
  expect(screen.getByLabelText('Formula')).toHaveFocus()
  expect(screen.getByLabelText('Formula')).toHaveValue('')
  expect(screen.queryByLabelText('Calculation type')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Description')).not.toBeInTheDocument()
  await user.type(screen.getByLabelText('Formula'), 'Double = ')
  await user.click(screen.getByRole('button', { name: /^FQTY$/ }))
  expect(screen.getByLabelText('Formula')).toHaveFocus()
  await user.keyboard('*2')
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      name: 'Double',
      formulaDefinition: 'Double = [FQTY]*2',
      definition: expect.objectContaining({
        nativeGrainId: 'base',
        calculationType: 'AggregateFirst',
        formula: expect.objectContaining({ displayText: '[FQTY]*2' }),
      }),
    }),
  )
})
it('retains advanced settings when collapsed and reports save errors', async () => {
  const { user, save } = setup()
  save.mockRejectedValue(new Error('Name already exists.'))
  await user.type(screen.getByLabelText('Formula'), 'Double = ')
  await user.type(screen.getByLabelText('Formula'), '1+2')
  await user.click(screen.getByRole('button', { name: 'Advanced settings' }))
  await user.selectOptions(screen.getByLabelText('Output grain'), 'dimension:week')
  await user.click(screen.getByRole('radio', { name: 'Calculate first' }))
  await user.click(screen.getByRole('button', { name: 'Hide advanced settings' }))
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Name already exists.'))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      definition: expect.objectContaining({
        nativeGrainId: 'week',
        calculationType: 'CalculateFirst',
        aggregation: expect.objectContaining({ nonTime: 'sum' }),
      }),
    }),
  )
})
it('validates formulas before saving and allows cancellation', async () => {
  const { user, save, close } = setup()
  await user.type(screen.getByLabelText('Formula'), 'Invalid = ABS(')
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(screen.getByRole('alert')).toBeVisible()
  expect(save).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(close).toHaveBeenCalledOnce()
})

it('uses CalculateFirst for conversion formulas and disables AggregateFirst', async () => {
  vi.mocked(metadataApi.request).mockImplementation(async (path, _method, body) => {
    if (path.endsWith('/formula/language')) return { functions: [] } as never
    if (path.includes('/formula/attributes') || path.includes('/formula/measures'))
      return [] as never
    const payload = body as { calculationType: string }
    return {
      name: 'Converted',
      expressionText: '[FQTY]*2',
      expression: { type: 'literal', value: 1 },
      valid: payload.calculationType === 'CalculateFirst',
      containsCounts: false,
      containsConversionFactors: true,
      resultType: { type: 'number', nullable: true },
      diagnostics: [],
    } as never
  })
  const { user, save } = setup()
  await user.type(screen.getByLabelText('Formula'), 'Converted = 1*2')
  await user.click(screen.getByRole('button', { name: 'Advanced settings' }))
  await waitFor(() => expect(screen.getByRole('radio', { name: 'Calculate first' })).toBeChecked())
  expect(screen.getByRole('radio', { name: 'Aggregate first' })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      definition: expect.objectContaining({ calculationType: 'CalculateFirst' }),
    }),
  )
})
