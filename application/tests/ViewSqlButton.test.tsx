import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { ViewSqlButton } from '../src/components/ViewSqlButton'

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '')
  }
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open')
  }
})

it('compiles the current definition on each opening and copies exactly the returned SQL', async () => {
  const user = userEvent.setup()
  const sql = "SELECT '<script>not HTML</script>' AS value"
  const load = vi.fn().mockResolvedValue({ sql, dialect: 'clickhouse' })
  const copy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
  render(<ViewSqlButton loadSql={load} />)
  expect(load).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'View SQL' }))
  expect(await screen.findByLabelText('Final ClickHouse SQL')).toHaveTextContent(sql)
  await user.click(screen.getByRole('button', { name: 'Copy SQL' }))
  expect(copy).toHaveBeenCalledWith(sql)
  expect(screen.getByText('Copied')).toBeVisible()
  await user.click(screen.getByRole('button', { name: 'Close' }))
  expect(screen.getByRole('button', { name: 'View SQL' })).toHaveFocus()
  load.mockResolvedValue({ sql: 'SELECT 2', dialect: 'clickhouse' })
  await user.click(screen.getByRole('button', { name: 'View SQL' }))
  expect(await screen.findByLabelText('Final ClickHouse SQL')).toHaveTextContent('SELECT 2')
  expect(load).toHaveBeenCalledTimes(2)
})

it('shows compilation errors and prevents copying stale SQL', async () => {
  const user = userEvent.setup()
  render(
    <ViewSqlButton
      loadSql={async () => {
        throw new Error('Choose a grain first.')
      }}
    />,
  )
  await user.click(screen.getByRole('button', { name: 'View SQL' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Choose a grain first.')
  expect(screen.getByRole('button', { name: 'Copy SQL' })).toBeDisabled()
})

it('aborts pending compilation when closed', async () => {
  const user = userEvent.setup()
  let signal: AbortSignal | undefined
  render(
    <ViewSqlButton
      loadSql={(value) => {
        signal = value
        return new Promise(() => {})
      }}
    />,
  )
  await user.click(screen.getByRole('button', { name: 'View SQL' }))
  await waitFor(() => expect(signal).toBeDefined())
  await user.click(screen.getByRole('button', { name: 'Close' }))
  expect(signal?.aborted).toBe(true)
})

it('labels recorded build SQL separately and reports incomplete captures', async () => {
  const user = userEvent.setup()
  const sql = "ALTER TABLE grain UPDATE qty=1 WHERE product='P1';"
  render(
    <ViewSqlButton
      label="View Build SQL"
      heading="Build SQL"
      description="Recorded statements"
      loadSql={async () => ({ sql, dialect: 'clickhouse', warning: '2 statements omitted.' })}
    />,
  )
  await user.click(screen.getByRole('button', { name: 'View Build SQL' }))
  expect(await screen.findByRole('heading', { name: 'Build SQL' })).toBeVisible()
  expect(screen.getByText('Recorded statements')).toBeVisible()
  expect(await screen.findByText('2 statements omitted.')).toBeVisible()
  expect(screen.getByLabelText('Final ClickHouse SQL')).toHaveTextContent(sql)
})
