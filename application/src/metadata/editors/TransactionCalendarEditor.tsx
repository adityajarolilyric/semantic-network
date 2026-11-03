import { useEffect, useState } from 'react'
import { metadataApi } from '../api'
import type { CalendarResolution, SourceColumn, PivotStage } from '../types'
import type { ResolutionCalendar } from './TransactionDateRangeEditor'
import { Checks, Select } from './Fields'
import { PlanningResult, type PlanningTableStatus } from '../PlanningTablePanel'

export function TransactionCalendarEditor({
  value,
  onChange,
  columns,
  onColumns,
  base,
  sourceId,
  transactionId,
  levelId,
  pivot,
}: {
  value: CalendarResolution
  onChange: (value: CalendarResolution) => void
  columns: SourceColumn[]
  onColumns: (columns: SourceColumn[]) => void
  base?: string
  sourceId: string
  transactionId: string
  levelId: string
  pivot?: PivotStage | null
}) {
  const [calendars, setCalendars] = useState<ResolutionCalendar[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<PlanningTableStatus | null>(null)
  const serialized = JSON.stringify(value)
  const pivotSignature = JSON.stringify(pivot ?? null)
  useEffect(() => {
    if (!base || !levelId) return
    const controller = new AbortController()
    void metadataApi
      .request<{ calendars: ResolutionCalendar[] }>(
        base + '/transactions/resolution/options?planningLevelId=' + encodeURIComponent(levelId),
        'GET',
        undefined,
        undefined,
        controller.signal,
      )
      .then((data) => {
        if (!controller.signal.aborted) setCalendars(data.calendars)
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message)
      })
    return () => controller.abort()
  }, [base, levelId])
  useEffect(() => {
    onColumns([])
    setPreview(null)
    setError('')
    if (
      !base ||
      !sourceId ||
      !value.dimensionId ||
      !value.startColumnId ||
      !value.endColumnId ||
      !value.selectedColumnIds.length
    )
      return
    const controller = new AbortController()
    void metadataApi
      .request<{ columns: SourceColumn[] }>(
        base + '/transactions/calendar/schema',
        'POST',
        {
          transactionId,
          sourceId,
          calendarResolution: JSON.parse(serialized),
          pivot: JSON.parse(pivotSignature),
        },
        undefined,
        controller.signal,
      )
      .then((data) => {
        if (!controller.signal.aborted) onColumns(data.columns)
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message)
      })
    return () => controller.abort()
    // The serialized configuration keeps requests independent of parent renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, sourceId, transactionId, serialized, pivotSignature, onColumns])
  const calendar = calendars.find((c) => c.dimensionId === value.dimensionId)
  const dateColumns = columns
    .filter((c) => /String|^(Date|Date32)$/.test(c.physicalType))
    .map((c) => ({ value: c.columnId, label: c.name }))
  return (
    <fieldset className="meta-subsection" disabled={busy}>
      <legend>Date range resolution</legend>
      <p className="meta-hint">
        Resolve every day from Start through End using your calendar, then map the calendar columns
        to the native grain. Source values are copied per day; measure Time aggregation combines
        days later.
      </p>
      <div className="meta-grid">
        <Select
          label="Start date column"
          value={value.startColumnId}
          items={dateColumns}
          onChange={(startColumnId) => onChange({ ...value, startColumnId })}
        />
        <Select
          label="End date column"
          value={value.endColumnId}
          items={dateColumns}
          onChange={(endColumnId) => onChange({ ...value, endColumnId })}
        />
        <Select
          label="Daily Time dimension"
          value={value.dimensionId}
          items={calendars.map((c) => ({ value: c.dimensionId, label: c.name }))}
          onChange={(dimensionId) => onChange({ ...value, dimensionId, selectedColumnIds: [] })}
        />
        {calendar && (
          <p className="meta-hint">
            Calendar date column: {calendar.dateColumnName}. Both range endpoints are included.
          </p>
        )}
      </div>
      {calendar && (
        <Checks
          label="Calendar columns to bring in"
          selected={value.selectedColumnIds}
          items={calendar.columns.map((c) => ({ value: c.columnId, label: c.label }))}
          onChange={(selectedColumnIds) => onChange({ ...value, selectedColumnIds })}
        />
      )}
      <button
        type="button"
        className="secondary-button"
        disabled={
          busy ||
          !base ||
          !sourceId ||
          !value.dimensionId ||
          !value.startColumnId ||
          !value.endColumnId ||
          !value.selectedColumnIds.length
        }
        onClick={async () => {
          setBusy(true)
          setPreview(null)
          setError('')
          try {
            setPreview(
              await metadataApi.request(base + '/transactions/calendar/preview', 'POST', {
                transactionId,
                sourceId,
                calendarResolution: value,
                pivot,
              }),
            )
          } catch (e) {
            setError(e instanceof Error ? e.message : 'Calendar resolution failed.')
          } finally {
            setBusy(false)
          }
        }}
      >
        {busy ? 'Resolving dates…' : 'Preview calendar data'}
      </button>
      {error && (
        <p role="alert" className="meta-error">
          {error}
        </p>
      )}
      {preview && (
        <button type="button" className="secondary-button" onClick={() => setPreview(null)}>
          Close calendar preview
        </button>
      )}
      {preview && (
        <PlanningResult data={preview} name="Calendar-resolved input" showRefresh={false} />
      )}
    </fieldset>
  )
}
