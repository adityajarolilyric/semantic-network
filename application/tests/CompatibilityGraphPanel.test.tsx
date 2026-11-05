import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import {
  CompatibilityGraphPanel,
  type CompatibilityGraph,
} from '../src/metadata/CompatibilityGraphPanel'
import { metadataApi } from '../src/metadata/api'
afterEach(() => vi.restoreAllMocks())
function setup() {
  const request = vi
    .spyOn(metadataApi, 'request')
    .mockImplementation(async (_path, _method, body) => {
      const payload = body as { measures: string[] }
      return {
        revisionVersion: 1,
        generationId: 'generation',
        levels: [{ planningLevelId: 'p', name: 'Product' }],
        selection: payload.measures.map((id) => ({
          measureId: id,
          name: id === 'f' ? 'Forecast' : 'Sales',
        })),
        contexts: [
          {
            planningLevelId: 'p',
            name: 'Product',
            selection: payload.measures.length
              ? { status: 'ready', reason: 'The complete selection can be plotted together.' }
              : null,
          },
        ],
        measures: [
          {
            measureId: 'f',
            name: 'Forecast',
            nativePlanningLevelId: 'p',
            nativePlanningLevelName: 'Product',
            connections: [{ planningLevelId: 'p', status: 'ready', reason: 'Native values.' }],
          },
          {
            measureId: 's',
            name: 'Sales',
            nativePlanningLevelId: 'p',
            nativePlanningLevelName: 'Product',
            connections: [
              {
                planningLevelId: 'p',
                status: 'needs_materialisation',
                reason: 'Build the Semantic Network.',
              },
            ],
          },
        ],
        totalMeasures: 2,
        hasMoreMeasures: false,
        hasMoreLevels: false,
      } as CompatibilityGraph as never
    })
  render(<CompatibilityGraphPanel base="/models/m/revisions/r" revisionVersion={1} />)
  return { request, user: userEvent.setup() }
}
it('requests whole selections, displays named contexts and explains readiness', async () => {
  const { request, user } = setup()
  await user.click(await screen.findByRole('button', { name: 'Select Forecast for compatibility' }))
  await user.click(screen.getByRole('button', { name: 'Select Sales for compatibility' }))
  await waitFor(() =>
    expect(request).toHaveBeenLastCalledWith(
      '/models/m/revisions/r/compatibility',
      'POST',
      expect.objectContaining({ measures: ['f', 's'] }),
      undefined,
      expect.any(AbortSignal),
    ),
  )
  expect(
    await screen.findByText('The full selection can be plotted together in a shown context.'),
  ).toBeVisible()
  expect(screen.getByText('Build the Semantic Network.')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Select Sales for compatibility' })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  expect(screen.queryByText('measure_f')).not.toBeInTheDocument()
})
it('filters through the read-only graph endpoint without triggering builds', async () => {
  const { request, user } = setup()
  await screen.findByRole('button', { name: 'Select Forecast for compatibility' })
  await user.type(screen.getByLabelText('Find measures'), 'Forecast')
  await waitFor(() =>
    expect(request).toHaveBeenLastCalledWith(
      '/models/m/revisions/r/compatibility',
      'POST',
      expect.objectContaining({ search: 'Forecast' }),
      undefined,
      expect.any(AbortSignal),
    ),
  )
  expect(request.mock.calls.every(([path]) => path.endsWith('/compatibility'))).toBe(true)
})
