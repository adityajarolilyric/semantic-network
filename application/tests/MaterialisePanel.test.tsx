import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { MaterialisePanel } from '../src/metadata/MaterialisePanel'
import { metadataApi } from '../src/metadata/api'
import type { Measure, PlanningLevel, EngineRun } from '../src/metadata/types'

function setup(reused = false) {
  vi.spyOn(metadataApi, 'request').mockResolvedValue({
    state: 'ready',
    columns: [],
    rows: [],
    rowCount: 0,
    unavailable: {},
    materialisation: [],
  } as never)
  const operation = vi
    .spyOn(metadataApi, 'managedOperation')
    .mockResolvedValue({ runId: 'run', state: 'succeeded', reused } as EngineRun)
  const onBuilt = vi.fn()
  render(
    <MaterialisePanel
      base="/models/m/revisions/r"
      revisionVersion={4}
      baseLevelId="base"
      levels={[{ objectId: 'base', name: 'Base' }] as PlanningLevel[]}
      measures={[{ objectId: 'FQTY', name: 'FQTY' }] as Measure[]}
      editable
      onBuilt={onBuilt}
    />,
  )
  return { operation, onBuilt, user: userEvent.setup() }
}

it('uses incremental builds and reports when existing values were reused', async () => {
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
})

it('requires confirmation for Full Refresh and sends the explicit full mode', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  const { user, operation } = setup()
  const refresh = screen.getByRole('button', { name: 'Full Refresh' })
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
  expect(await screen.findByText('Full Refresh completed.')).toBeVisible()
  expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('replaces manual edits'))
})
