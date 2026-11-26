import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { UploadPanel } from '../src/components/UploadPanel'
import { ApiError, warehouseApi } from '../src/lib/api'
import type { CsvImportResult } from '../src/types'

const csv = (name: string) => new File(['id,qty\nP1,10\n'], name, { type: 'text/csv' })
const result = (table: string): CsvImportResult => ({
  table,
  rowCount: 1,
  columns: [{ name: 'qty', type: 'Int64' }],
  replayed: false,
  warnings: [],
  status: 'created',
})
function setup(maxBytes = 10_485_760) {
  const imported = vi.fn().mockResolvedValue(undefined)
  const upload = vi
    .spyOn(warehouseApi, 'upload')
    .mockImplementation(async (_file, table) => result(table))
  render(<UploadPanel maxRows={5000} maxBytes={maxBytes} onImported={imported} />)
  return { imported, upload, user: userEvent.setup() }
}

it('accepts ten files and processes only one request at a time with a stable batch total', async () => {
  const { upload, imported, user } = setup()
  let finishFirst!: (value: CsvImportResult) => void
  let finishSecond!: (value: CsvImportResult) => void
  upload
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishFirst = resolve
        }),
    )
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishSecond = resolve
        }),
    )
  await user.upload(
    screen.getByLabelText('CSV files'),
    Array.from({ length: 10 }, (_, index) => csv(`data_${index + 1}.csv`)),
  )
  await user.click(screen.getByRole('button', { name: 'Upload & create 10 tables' }))
  expect(upload).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('button', { name: 'Choose files' })).toBeDisabled()
  expect(screen.getByLabelText('Table name for data_2.csv')).toBeDisabled()
  await act(async () => finishFirst(result('data_1')))
  await waitFor(() => expect(upload).toHaveBeenCalledTimes(2))
  expect(screen.getByText(/Uploading 2 of 10: data_2.csv/)).toBeVisible()
  expect(imported).toHaveBeenCalledTimes(1)
  await act(async () => finishSecond(result('data_2')))
  await screen.findByText('10 of 10 tables created.')
  expect(upload.mock.calls.map(([, name]) => name)).toEqual(
    Array.from({ length: 10 }, (_, i) => `data_${i + 1}`),
  )
  expect(imported).toHaveBeenCalledTimes(10)
  expect(new Set(upload.mock.calls.map(([, , key]) => key)).size).toBe(10)
})

it('continues after a file fails and retries only that file with its unchanged import key', async () => {
  const { upload, imported, user } = setup()
  upload.mockRejectedValueOnce(
    new ApiError('Existing table. Choose another name.', 'conflict', 409, 'request-a'),
  )
  await user.upload(screen.getByLabelText('CSV files'), [csv('a.csv'), csv('b.csv'), csv('c.csv')])
  await user.click(screen.getByRole('button', { name: 'Upload & create 3 tables' }))
  await screen.findByText('2 of 3 tables created. Correct or retry the remaining files.')
  expect(screen.getByRole('alert')).toHaveTextContent('Reference: request-a')
  expect(imported).toHaveBeenCalledTimes(2)
  const key = upload.mock.calls[0][2]
  await user.click(screen.getByRole('button', { name: 'Retry 1 file' }))
  await screen.findByText('3 of 3 tables created.')
  expect(upload.mock.calls.map(([, name]) => name)).toEqual(['a', 'b', 'c', 'a'])
  expect(upload.mock.calls[3][2]).toBe(key)
})

it('blocks more than ten files without discarding an already selected valid batch', async () => {
  const { upload, user } = setup()
  await user.upload(screen.getByLabelText('CSV files'), [csv('a.csv'), csv('b.csv')])
  await user.upload(
    screen.getByLabelText('CSV files'),
    Array.from({ length: 11 }, (_, i) => csv(`${i}.csv`)),
  )
  expect(screen.getByRole('alert')).toHaveTextContent('between 1 and 10')
  expect(screen.getByLabelText('Table name for a.csv')).toHaveValue('a')
  expect(upload).not.toHaveBeenCalled()
})

it.each([
  [csv('invalid.txt'), 'choose a .csv file'],
  [new File(['x'.repeat(1025)], 'oversized.csv', { type: 'text/csv' }), 'too large'],
])('validates every dropped file before replacing the queue', (invalid, message) => {
  const { upload } = setup(1024)
  fireEvent.drop(screen.getByText('Drop up to 10 CSV files here').parentElement!, {
    dataTransfer: { files: [csv('valid.csv'), invalid] },
  })
  expect(screen.getByRole('alert')).toHaveTextContent(message)
  expect(screen.queryByLabelText('CSV upload queue')).not.toBeInTheDocument()
  expect(upload).not.toHaveBeenCalled()
})

it('rejects duplicate edited table names before submitting any request', async () => {
  const { upload, user } = setup()
  await user.upload(screen.getByLabelText('CSV files'), [csv('a.csv'), csv('b.csv')])
  await user.clear(screen.getByLabelText('Table name for b.csv'))
  await user.type(screen.getByLabelText('Table name for b.csv'), 'a')
  await user.click(screen.getByRole('button', { name: 'Upload & create 2 tables' }))
  expect(screen.getByRole('alert')).toHaveTextContent('different table name')
  expect(upload).not.toHaveBeenCalled()
})

it('does not retry a created table when its catalog refresh fails, and continues the batch', async () => {
  const { imported, upload, user } = setup()
  imported.mockRejectedValueOnce(new Error('Catalog offline'))
  await user.upload(screen.getByLabelText('CSV files'), [csv('a.csv'), csv('b.csv')])
  await user.click(screen.getByRole('button', { name: 'Upload & create 2 tables' }))
  await screen.findByText('2 of 2 tables created.')
  expect(screen.getByText(/Table created, but the catalog could not refresh/)).toBeVisible()
  expect(upload).toHaveBeenCalledTimes(2)
  expect(screen.queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument()
})

it('uses unique suggested names for collisions and permits removing a file before uploading', async () => {
  const { upload, user } = setup()
  await user.upload(screen.getByLabelText('CSV files'), [
    csv('Sales Report.csv'),
    csv('Sales-Report.csv'),
  ])
  expect(screen.getByLabelText('Table name for Sales Report.csv')).toHaveValue('Sales_Report')
  expect(screen.getByLabelText('Table name for Sales-Report.csv')).toHaveValue('Sales_Report_2')
  await user.click(screen.getByRole('button', { name: 'Remove Sales-Report.csv' }))
  expect(screen.getByLabelText('Table name')).toHaveValue('Sales_Report')
  await user.click(screen.getByRole('button', { name: 'Upload & create table' }))
  await waitFor(() => expect(upload).toHaveBeenCalledTimes(1))
})

it('retries incomplete semantic setup using the same physical import key', async () => {
  const { upload, imported, user } = setup()
  upload.mockResolvedValueOnce({
    ...result('sales'),
    semanticSetupComplete: false,
    warnings: ['Automatic semantic setup incomplete.'],
  })
  await user.upload(screen.getByLabelText('CSV files'), csv('sales.csv'))
  await user.click(screen.getByRole('button', { name: 'Upload & create table' }))
  await screen.findByText(
    'CSV stored. Retry to complete semantic setup without importing it again.',
  )
  expect(imported).toHaveBeenCalledTimes(1)
  const originalKey = upload.mock.calls[0][2]
  upload.mockResolvedValueOnce({ ...result('sales'), replayed: true, semanticSetupComplete: true })
  await user.click(screen.getByRole('button', { name: 'Retry 1 file' }))
  await waitFor(() => expect(upload).toHaveBeenCalledTimes(2))
  expect(upload.mock.calls[1][2]).toBe(originalKey)
  await waitFor(() =>
    expect(screen.queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument(),
  )
})
