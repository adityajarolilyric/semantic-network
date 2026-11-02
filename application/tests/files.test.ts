import { describe, expect, it } from 'vitest'
import { formatBytes, suggestTableName, suggestTableNames, validTableName } from '../src/lib/files'

describe('CSV table naming', () => {
  it('suggests a valid new name without changing column headers', () => {
    expect(suggestTableName('Sourcing Matrix.csv')).toBe('Sourcing_Matrix')
    expect(suggestTableName('2026-sales.CSV')).toBe('table_2026_sales')
    expect(suggestTableName('!!!.csv')).toBe('uploaded_table')
    expect(suggestTableName('a'.repeat(200) + '.csv')).toHaveLength(128)
  })
  it('rejects SQL fragments and private staging names', () => {
    expect(validTableName('Forecast')).toBe(true)
    expect(validTableName('3_forecast')).toBe(false)
    expect(validTableName('a; DROP TABLE b')).toBe(false)
    expect(validTableName('__csv_ingest_x')).toBe(false)
  })
  it('formats actual file sizes', () => {
    expect(formatBytes(100)).toBe('1 KB')
    expect(formatBytes(10_485_760)).toBe('10.0 MB')
  })
})

it('disambiguates sanitized names within the identifier length limit', () => {
  const names = suggestTableNames([
    'A B.csv',
    'A-B.csv',
    'A_B_2.csv',
    'a'.repeat(140) + '.csv',
    'a'.repeat(140) + '.CSV',
  ])
  expect(names.slice(0, 3)).toEqual(['A_B', 'A_B_2', 'A_B_2_2'])
  expect(new Set(names).size).toBe(5)
  expect(names.every((name) => validTableName(name))).toBe(true)
})
