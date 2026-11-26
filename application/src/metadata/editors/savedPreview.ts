import { inputOf, type ObjectInput } from '../types'

/** Preview is always the committed object view; draft SQL never replaces it. */
export async function saveBeforePreview<T extends ObjectInput>(
  value: T,
  initial: T,
  editing: boolean,
  save?: (input: T) => Promise<void>,
): Promise<boolean> {
  const authored = (input: T) =>
    JSON.stringify(inputOf(input), (key, field) => (key === 'measures' ? undefined : field))
  if (editing && authored(value) === authored(initial)) return true
  if (!window.confirm('Save changes before previewing the transformed view?')) return false
  if (!save) throw new Error('Save this object first, then open Preview.')
  await save(value)
  return true
}
