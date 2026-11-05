import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { QuickCalculatedMeasure } from '../src/metadata/QuickCalculatedMeasure'
import { metadataApi } from '../src/metadata/api'
import { emptySnapshot } from '../src/metadata/types'

beforeEach(() => {
  vi.spyOn(metadataApi, 'request').mockImplementation(async (path, _method, body) => {
    if (path.endsWith('/formula/language')) return { functions: [] } as never
    if (path.includes('/formula/attributes')) return [] as never
    const text = (body as { displayText: string }).displayText
    return {
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
  data.planningLevels = ['base', 'week'].map((id) => ({
    ...envelope,
    kind: 'planningLevel',
    objectId: id,
    name: id,
    definition: {
      population: { operation: 'source', sourceId: 's' },
      grain: { keyIds: [], columns: [] },
      dimensionBindings: [],
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
        nativePlanningLevelId: 'base',
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
  expect(screen.getByLabelText('Name')).toHaveFocus()
  expect(screen.getByLabelText('Formula')).toHaveValue('')
  expect(screen.queryByLabelText('Calculation type')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Description')).not.toBeInTheDocument()
  await user.type(screen.getByLabelText('Name'), 'Double')
  await user.click(screen.getByRole('button', { name: /^FQTY$/ }))
  expect(screen.getByLabelText('Formula')).toHaveFocus()
  await user.keyboard('*2')
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      name: 'Double',
      definition: expect.objectContaining({
        nativePlanningLevelId: 'base',
        calculationType: 'AggregateFirst',
        formula: expect.objectContaining({ displayText: '[FQTY]*2' }),
      }),
    }),
  )
})
it('retains advanced settings when collapsed and reports save errors', async () => {
  const { user, save } = setup()
  save.mockRejectedValue(new Error('Name already exists.'))
  await user.type(screen.getByLabelText('Name'), 'Double')
  await user.type(screen.getByLabelText('Formula'), '1+2')
  await user.click(screen.getByRole('button', { name: 'Advanced settings' }))
  await user.selectOptions(screen.getByLabelText('Output Planning Level'), 'week')
  await user.click(screen.getByRole('radio', { name: 'Calculate first' }))
  await user.click(screen.getByRole('button', { name: 'Hide advanced settings' }))
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Name already exists.'))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      definition: expect.objectContaining({
        nativePlanningLevelId: 'week',
        calculationType: 'CalculateFirst',
        aggregation: expect.objectContaining({ nonTime: 'sum' }),
      }),
    }),
  )
})
it('validates formulas before saving and allows cancellation', async () => {
  const { user, save, close } = setup()
  await user.type(screen.getByLabelText('Name'), 'Invalid')
  await user.type(screen.getByLabelText('Formula'), 'ABS(')
  await user.click(screen.getByRole('button', { name: 'Save metadata' }))
  expect(screen.getByRole('alert')).toBeVisible()
  expect(save).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(close).toHaveBeenCalledOnce()
})
