import type { ResolutionDateRange, SourceColumn } from '../types'
import { Select } from './Fields'

export type ResolutionCalendar = {
  memberCatalogId: string
  name: string
  dateColumnId: string
  dateColumnName: string
  columns: { columnId: string; label: string }[]
  suggestions: Record<string, string>
}
export function newDateRange(
  calendars: ResolutionCalendar[],
  grainId: string,
): ResolutionDateRange {
  const calendar = calendars.length === 1 ? calendars[0] : undefined
  return {
    memberCatalogId: calendar?.memberCatalogId ?? '',
    calendarKeyColumnId: calendar?.suggestions[grainId] ?? '',
    startColumnId: '',
    endColumnId: '',
  }
}
export function TransactionDateRangeEditor({
  value,
  onChange,
  calendars,
  grainId,
  name,
  columns,
}: {
  value: ResolutionDateRange
  onChange: (value: ResolutionDateRange) => void
  calendars: ResolutionCalendar[]
  grainId: string
  name: string
  columns: SourceColumn[]
}) {
  const calendar = calendars.find((c) => c.memberCatalogId === value.memberCatalogId)
  const endpoints = columns
    .filter(
      (c) => /^(Date32|Date|String)$/.test(c.physicalType) || c.physicalType.includes('String'),
    )
    .map((c) => ({ value: c.columnId, label: c.name }))
  return (
    <div className="resolution-range">
      <p className="meta-hint">
        Start and End are inclusive. Values and overrides resolve per calendar day, then map to{' '}
        {name}. Weekly totals use the measure’s configured aggregation.
      </p>
      {!calendars.length && (
        <p className="meta-error">
          Configure a daily Time dimension with a Day-to-period mapping before using date ranges.
        </p>
      )}
      <div className="meta-grid">
        <Select
          label="Range Time dimension"
          value={value.memberCatalogId}
          items={calendars.map((c) => ({ value: c.memberCatalogId, label: c.name }))}
          onChange={(memberCatalogId) =>
            onChange({
              ...value,
              memberCatalogId,
              calendarKeyColumnId:
                calendars.find((c) => c.memberCatalogId === memberCatalogId)?.suggestions[grainId] ?? '',
            })
          }
        />
        <Select
          label={'Calendar column for ' + name}
          value={value.calendarKeyColumnId}
          items={(calendar?.columns ?? [])
            .filter(
              (c) =>
                !calendar?.suggestions[grainId] || c.columnId === calendar.suggestions[grainId],
            )
            .map((c) => ({ value: c.columnId, label: c.label }))}
          onChange={(calendarKeyColumnId) => onChange({ ...value, calendarKeyColumnId })}
        />
        <Select
          label="Range start column"
          value={value.startColumnId}
          items={endpoints}
          onChange={(startColumnId) => onChange({ ...value, startColumnId })}
        />
        <Select
          label="Range end column"
          value={value.endColumnId}
          items={endpoints}
          onChange={(endColumnId) => onChange({ ...value, endColumnId })}
        />
      </div>
      {calendar && (
        <p className="meta-hint">
          Calendar date: {calendar.dateColumnName}. Each date must map to one {name} value. Missing
          calendar or planning coverage fails instead of silently clipping the range.
        </p>
      )}
    </div>
  )
}
