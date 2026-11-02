export function suggestTableName(filename: string): string {
  const name = filename
    .replace(/\.csv$/i, '')
    .replace(/[^A-Za-z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '')
  const candidate = /^[0-9]/.test(name) ? `table_${name}` : name
  return (candidate || 'uploaded_table').slice(0, 128)
}

export function suggestTableNames(filenames: string[]): string[] {
  const used = new Set<string>()
  return filenames.map((filename) => {
    const base = suggestTableName(filename)
    let name = base
    for (let index = 2; used.has(name); index++) {
      const suffix = `_${index}`
      name = base.slice(0, 128 - suffix.length) + suffix
    }
    used.add(name)
    return name
  })
}

export function formatBytes(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.ceil(bytes / 1024))} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function validTableName(name: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(name) && !name.startsWith('__csv_ingest_')
}
