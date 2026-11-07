import { useEffect, useState } from 'react'
import { metadataApi } from './api'
import type { Expression, MeasureCommon } from './types'

export type FormulaValidation = {
  valid: boolean
  expression?: Expression
  containsCounts: boolean
  resultType?: MeasureCommon['resultType']
  unit?: string | null
  diagnostics: { message: string; line?: number; column?: number }[]
}
export type FormulaRequest = {
  objectId: string
  name: string
  displayText: string
  nativeGrainId: string
  calculationType: 'AggregateFirst' | 'CalculateFirst'
  languageVersion: 'planning-v1'
}
export function validateFormula(base: string, payload: FormulaRequest, signal?: AbortSignal) {
  return metadataApi.request<FormulaValidation>(
    base + '/measures/formula/validate',
    'POST',
    payload,
    undefined,
    signal,
  )
}
export function useFormulaValidation(base: string | undefined, payload: FormulaRequest | null) {
  const key = JSON.stringify(payload)
  const [result, setResult] = useState<{ key: string; value: FormulaValidation } | null>(null)
  useEffect(() => {
    if (!base || !payload || !payload.displayText.trim() || !payload.nativeGrainId) return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      void validateFormula(base, payload, controller.signal)
        .then((value) => {
          if (!controller.signal.aborted) setResult({ key, value })
        })
        .catch((error) => {
          if (!controller.signal.aborted)
            setResult({
              key,
              value: {
                valid: false,
                containsCounts: false,
                diagnostics: [{ message: error.message }],
              },
            })
        })
    }, 300)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [base, key]) // key includes every validation input
  return result?.key === key ? result.value : null
}
