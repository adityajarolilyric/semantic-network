import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { DecisionViewNetwork } from '../src/metadata/DecisionViewNetwork'
import { metadataApi } from '../src/metadata/api'
import type { Measure, EngineRun } from '../src/metadata/types'

vi.mock('../src/metadata/DecisionViewPanel', () => ({
  DecisionViewPanel: ({
    networkActions,
    materialisedPreview,
    onBuildRequiredChange,
  }: {
    networkActions: React.ReactNode
    materialisedPreview: React.ReactNode
    onBuildRequiredChange: (message: string) => void
  }) => (
    <section aria-label="Decision View">
      {networkActions}
      <button onClick={() => onBuildRequiredChange('Build Semantic Network is required.')}>
        Report build required
      </button>
      <button onClick={() => onBuildRequiredChange('')}>Report network ready</button>
      <div data-testid="pivot">Pivot</div>
      {materialisedPreview}
    </section>
  ),
}))
it('marks the build button with the build-required message on hover', async () => {
  const { user } = setup()
  await user.click(screen.getByRole('button', { name: 'Report build required' }))
  expect(screen.getByTitle('Build Semantic Network is required.')).toHaveTextContent(
    'Build Semantic Network',
  )
  expect(screen.getByRole('img', { name: 'Semantic Network build required' })).toBeVisible()
  await user.click(screen.getByRole('button', { name: 'Report network ready' }))
  expect(screen.queryByTitle('Build Semantic Network is required.')).not.toBeInTheDocument()
  expect(
    screen.queryByRole('img', { name: 'Semantic Network build required' }),
  ).not.toBeInTheDocument()
})
function setup(reused = false) {
  const request = vi.spyOn(metadataApi, 'request').mockImplementation(
    async (path) =>
      ({
        state: path.includes('/unbuilt?') ? 'not_built' : 'ready',
        columns: [{ columnId: 'qty', name: 'Quantity', physicalType: 'Float64' }],
        rows: path.includes('preview=true') ? [['12.5']] : [],
        rowCount: path.includes('/unbuilt?') ? null : 159,
        unavailable: {},
        materialisation: [],
      }) as never,
  )
  const operation = vi
    .spyOn(metadataApi, 'managedOperation')
    .mockResolvedValue({ runId: 'run', state: 'succeeded', reused } as EngineRun)
  const onBuilt = vi.fn()
  render(
    <DecisionViewNetwork
      base="/models/m/revisions/r"
      revisionVersion={4}
      baseLevelId="base"
      levels={[
        { objectId: 'base', name: 'Base' },
        { objectId: 'other', name: 'Other' },
        { objectId: 'unbuilt', name: 'Unbuilt' },
      ]}
      measures={[{ objectId: 'FQTY', name: 'FQTY' }] as Measure[]}
      editable
      onBuilt={onBuilt}
    />,
  )
  return { request, operation, onBuilt, user: userEvent.setup() }
}
it('builds incrementally from Decision View without selecting a preview table', async () => {
  const confirm = vi.spyOn(window, 'confirm')
  const { user, operation, onBuilt } = setup(true)
  await user.click(screen.getByRole('button', { name: 'Build Semantic Network' }))
  expect(await screen.findByText('Already up to date. Existing values were reused.')).toBeVisible()
  expect(operation).toHaveBeenCalledWith(
    '/models/m/revisions/r/measures/execution/build',
    { mode: 'incremental' },
    4,
  )
  expect(confirm).not.toHaveBeenCalled()
  expect(onBuilt).toHaveBeenCalledOnce()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
})
it('requires confirmation for Full Semantic Refresh', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  const { user, operation } = setup()
  const refresh = screen.getByRole('button', { name: 'Full Semantic Refresh' })
  await user.click(refresh)
  expect(operation).not.toHaveBeenCalled()
  confirm.mockReturnValue(true)
  await user.click(refresh)
  await waitFor(() =>
    expect(operation).toHaveBeenCalledWith(
      '/models/m/revisions/r/measures/execution/build',
      { mode: 'full' },
      4,
    ),
  )
  expect(await screen.findByText('Full Semantic Refresh completed.')).toBeVisible()
  expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('source-backed edits remain in the source'))
})
it('lists only materialised tables below the pivot and loads rows on selection', async () => {
  const { user, request } = setup()
  const selector = screen.getByLabelText('Materialised table')
  await waitFor(() => expect(selector).toBeEnabled())
  expect(within(selector).getByRole('option', { name: 'Base' })).toBeVisible()
  expect(within(selector).queryByRole('option', { name: 'Unbuilt' })).not.toBeInTheDocument()
  expect(request.mock.calls.every(([path]) => !path.includes('preview=true'))).toBe(true)
  expect(
    screen.getByTestId('pivot').compareDocumentPosition(selector) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy()
  await user.selectOptions(selector, 'base')
  expect(await screen.findByRole('columnheader', { name: /^Quantity/ })).toBeVisible()
  expect(screen.getByText('12.50')).toBeVisible()
  expect(screen.getByText(/1\s*\/\s*159/)).toBeVisible()
  await user.selectOptions(selector, 'other')
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/models/m/revisions/r/measures/execution/levels/other?preview=true',
      'GET',
      undefined,
      undefined,
      expect.any(AbortSignal),
    ),
  )
  await user.selectOptions(selector, '')
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
})
it('prevents another network operation while a build is pending', async () => {
  const { user, operation } = setup()
  let complete!: (value: EngineRun) => void
  operation.mockReturnValue(
    new Promise<EngineRun>((resolve) => {
      complete = resolve
    }),
  )
  await user.click(screen.getByRole('button', { name: 'Build Semantic Network' }))
  expect(screen.getByRole('button', { name: 'Building Semantic Network…' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Full Semantic Refresh' })).toBeDisabled()
  complete({ state: 'succeeded', runId: 'run' } as EngineRun)
  expect(await screen.findByText('Semantic Network updated.')).toBeVisible()
})
