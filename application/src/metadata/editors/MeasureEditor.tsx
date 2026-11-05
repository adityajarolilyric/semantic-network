import { CompatibilityGraphPanel } from '../CompatibilityGraphPanel'
import { FormulaBuilder, type FormulaFunction } from '../FormulaBuilder'
import { metadataApi } from '../api'
import { MeasureBaseVisibility } from './MeasureBaseVisibility'
import { FieldHelp } from '../../components/FieldHelp'
import { useEffect, useId, useState } from 'react'
import { formatFormula, formulaUnit } from '../formula'
import {
  typedResultType,
  numericType,
  aggregationOptions,
  aggregationForType,
} from '../measureTypes'
import { useFormulaValidation, validateFormula, type FormulaRequest } from '../useFormulaValidation'
import type { FormulaAttribute } from '../formula'
import type { Aggregation, CalcDefinition, Measure, RefDefinition, SourceColumn } from '../types'
import { Actions, Check, IdentityFields, options, Select, Text, type EditorProps } from './Fields'
export const defaultAggregation: Aggregation = {
  nonTime: 'sum',
  time: 'sum',
  nullInputPolicy: 'ignore',
  allNullResult: 'null',
}
export function MeasureEditor({
  initial,
  data,
  editing,
  onSave,
  onCancel,
  busy,
  base,
  baseLevelId,
  quick = false,
}: EditorProps<Measure> & { base?: string; baseLevelId?: string | null; quick?: boolean }) {
  const [advanced, setAdvanced] = useState(false)
  const [showCompatibility, setShowCompatibility] = useState(false)
  const showSettings = !quick || advanced
  const [value, set] = useState(initial)
  const [text, setText] = useState(
    !quick && initial.definition.measureType === 'calculated'
      ? formatFormula(
          initial.definition.formula.expression,
          data.measures,
          data.planningLevels.find((l) => l.objectId === initial.definition.nativePlanningLevelId)
            ?.definition.grain.columns,
          undefined,
          data.planningLevels.find((l) => l.objectId === initial.definition.nativePlanningLevelId),
        )
      : '',
  )
  const [error, setError] = useState('')
  const [validatingSave, setValidatingSave] = useState(false)
  const d = value.definition
  const [attributes, setAttributes] = useState<FormulaAttribute[]>([])
  const [attributeError, setAttributeError] = useState('')
  const [functions, setFunctions] = useState<FormulaFunction[]>([])
  const [functionError, setFunctionError] = useState('')
  useEffect(() => {
    setFunctions([])
    setFunctionError('')
    if (!base) return
    const controller = new AbortController()
    void metadataApi
      .request<{ functions: FormulaFunction[] }>(
        base + '/measures/formula/language',
        'GET',
        undefined,
        undefined,
        controller.signal,
      )
      .then((result) => {
        if (!controller.signal.aborted) setFunctions(result.functions)
      })
      .catch((error) => {
        if (!controller.signal.aborted) setFunctionError(error.message)
      })
    return () => controller.abort()
  }, [base])
  useEffect(() => {
    setAttributes([])
    setAttributeError('')
    if (!base || d.measureType !== 'calculated' || !d.nativePlanningLevelId) return
    const controller = new AbortController()
    void metadataApi
      .request<FormulaAttribute[]>(
        base +
          '/measures/formula/attributes?planningLevelId=' +
          encodeURIComponent(d.nativePlanningLevelId),
        'GET',
        undefined,
        undefined,
        controller.signal,
      )
      .then((items) => {
        if (!controller.signal.aborted) setAttributes(items)
      })
      .catch((error) => {
        if (!controller.signal.aborted) setAttributeError(error.message)
      })
    return () => controller.abort()
  }, [base, d.measureType, d.nativePlanningLevelId])
  useEffect(() => {
    if (!attributes.length || initial.definition.measureType !== 'calculated') return
    const original = formatFormula(
      initial.definition.formula.expression,
      data.measures,
      data.planningLevels.find(
        (level) => level.objectId === initial.definition.nativePlanningLevelId,
      )?.definition.grain.columns,
      undefined,
      data.planningLevels.find((l) => l.objectId === initial.definition.nativePlanningLevelId),
    )
    setText((current) =>
      current === original
        ? formatFormula(
            initial.definition.measureType === 'calculated'
              ? initial.definition.formula.expression
              : { type: 'literal', value: 0 },
            data.measures,
            data.planningLevels.find((l) => l.objectId === initial.definition.nativePlanningLevelId)
              ?.definition.grain.columns,
            attributes,
            data.planningLevels.find(
              (l) => l.objectId === initial.definition.nativePlanningLevelId,
            ),
          )
        : current,
    )
  }, [attributes])
  const update = (patch: Partial<typeof d>) =>
    set({ ...value, definition: { ...d, ...patch } as Measure['definition'] })
  const tx =
    d.measureType === 'referenced'
      ? data.transactions.find((t) => t.objectId === d.transactionId)
      : undefined
  const source = data.sources.find((s) => s.objectId === tx?.definition.sourceId)
  const transformedTransaction =
    !!tx?.definition.pivot?.enabled || !!tx?.definition.calendarResolution
  const schemaKey =
    base && tx ? base + '/transactions/' + encodeURIComponent(tx.objectId) + '/schema' : ''
  const [transactionSchema, setTransactionSchema] = useState<{
    key: string
    columns: SourceColumn[]
    valueColumnIds?: string[]
  } | null>(null)
  const [schemaError, setSchemaError] = useState('')
  useEffect(() => {
    setSchemaError('')
    if (!schemaKey || !transformedTransaction) return
    const controller = new AbortController()
    void metadataApi
      .request<{ columns: SourceColumn[]; valueColumnIds?: string[] }>(
        schemaKey,
        'GET',
        undefined,
        undefined,
        controller.signal,
      )
      .then((result) => {
        if (!controller.signal.aborted) setTransactionSchema({ key: schemaKey, ...result })
      })
      .catch((e) => {
        if (!controller.signal.aborted) setSchemaError(e.message)
      })
    return () => controller.abort()
  }, [schemaKey, transformedTransaction, tx?.version])
  const effectiveColumns = transformedTransaction
    ? transactionSchema?.key === schemaKey
      ? transactionSchema.columns
      : []
    : (source?.definition.columns ?? [])
  const valueColumns =
    tx?.definition.mappingMode === 'resolve_multi_grain'
      ? effectiveColumns
          .filter(
            (c) =>
              tx.definition.resolution?.values.some((v) => v.sourceColumnId === c.columnId) ||
              !!tx.definition.resolution?.passthroughColumnIds.includes(c.columnId),
          )
          .map((c) => ({
            ...c,
            physicalType: tx.definition.resolution?.values.some(
              (value) => value.sourceColumnId === c.columnId,
            )
              ? 'Float64'
              : c.physicalType,
          }))
      : effectiveColumns
  const savedAggregation = d.aggregation ?? defaultAggregation
  const valueColumn =
    d.measureType === 'referenced'
      ? valueColumns.find((c) => c.columnId === d.valueColumnId)
      : undefined
  const sourceResultType =
    d.measureType === 'referenced' && valueColumn
      ? typedResultType(
          valueColumn.physicalType,
          valueColumn.nullable ||
            d.missingTransactionValue === 'null' ||
            savedAggregation.allNullResult === 'null',
        )
      : null
  const formulaRequest: FormulaRequest | null =
    d.measureType === 'calculated'
      ? {
          objectId: value.objectId,
          name: value.name,
          displayText: text,
          languageVersion: 'planning-v1',
          nativePlanningLevelId: d.nativePlanningLevelId,
          calculationType: d.calculationType,
        }
      : null
  const validation = useFormulaValidation(base, formulaRequest)
  const formulaInputs = new Set<string>()
  function collectInputs(node: import('../types').Expression | undefined) {
    if (!node) return
    if (node.type === 'measureRef') formulaInputs.add(node.measureId)
    else if (node.type === 'binary') {
      collectInputs(node.left)
      collectInputs(node.right)
    } else if (node.type === 'call' || node.type === 'count') node.arguments.forEach(collectInputs)
  }
  collectInputs(validation?.expression)
  const counted =
    d.measureType === 'calculated' &&
    (validation?.expression
      ? validation.containsCounts
      : /\bCOUNT(?:_IF|_DISTINCT|_ROWS)?\s*\(/i.test(text))
  useEffect(() => {
    if (
      d.measureType === 'calculated' &&
      validation?.containsCounts &&
      d.calculationType !== 'AggregateFirst'
    ) {
      update({ calculationType: 'AggregateFirst', aggregation: null, allocation: null })
    }
  }, [validation, d.measureType, d.measureType === 'calculated' ? d.calculationType : null])
  let resultKind = sourceResultType?.type ?? validation?.resultType?.type ?? d.resultType.type
  if (numericType(resultKind)) resultKind = 'number'
  const numeric = numericType(resultKind)
  const nonTimeOptions = aggregationOptions(resultKind)
  const timeOptions = aggregationOptions(resultKind, true)
  const aggregation = aggregationForType(savedAggregation, resultKind)
  const typeGroupId = useId()
  const calculationTypeGroupId = useId()
  const changeMeasureType = (type: 'referenced' | 'calculated') => {
    const common = {
      nativePlanningLevelId: '',
      resultType: { type: 'number' as const, nullable: true },
      editable: false,
    }
    const definition: RefDefinition | CalcDefinition =
      type === 'referenced'
        ? {
            ...common,
            measureType: 'referenced',
            transactionId: '',
            valueColumnId: '',
            aggregation: defaultAggregation,
            missingTransactionValue: 'null',
            sourceNullPolicy: 'preserve',
            allocation: { mode: 'weighted_by_self' },
          }
        : {
            ...common,
            measureType: 'calculated',
            calculationType: 'AggregateFirst',
            formula: { displayText: '', expression: { type: 'literal', value: 0 } },
          }
    set({ ...value, definition })
    setError('')
  }
  const calculationTypeControl =
    d.measureType === 'calculated' ? (
      <fieldset className="measure-type-control" disabled={busy || validatingSave}>
        <legend>
          Calculation type <FieldHelp label="Calculation type" />
        </legend>
        <div className="measure-type-switch">
          {(
            [
              ['AggregateFirst', 'Aggregate first'],
              ['CalculateFirst', 'Calculate first'],
            ] as const
          ).map(([type, label]) => (
            <label key={type} className="measure-type-option">
              <input
                type="radio"
                name={calculationTypeGroupId}
                value={type}
                checked={d.calculationType === type}
                disabled={counted && type === 'CalculateFirst'}
                onChange={() =>
                  update({
                    calculationType: type,
                    aggregation:
                      type === 'CalculateFirst'
                        ? aggregationForType(defaultAggregation, resultKind)
                        : null,
                    allocation:
                      type === 'CalculateFirst' && numeric ? { mode: 'weighted_by_self' } : null,
                  })
                }
              />
              <span>{label}</span>
            </label>
          ))}
        </div>
      </fieldset>
    ) : null
  const outputLevelControl =
    d.measureType === 'calculated' ? (
      <div className="measure-output-level">
        <Select
          label="Output Planning Level"
          value={d.nativePlanningLevelId}
          items={options(data.planningLevels)}
          onChange={(nativePlanningLevelId) => update({ nativePlanningLevelId })}
        />
      </div>
    ) : (
      <div className="measure-output-level" role="group" aria-label="Output Planning Level">
        <span className="field-label-with-help">
          Output Planning Level <FieldHelp label="Output Planning Level" />
        </span>
        <strong>
          {data.planningLevels.find((level) => level.objectId === d.nativePlanningLevelId)?.name ??
            'Choose a transaction'}
        </strong>
      </div>
    )
  const calculationSettings =
    d.measureType === 'calculated' ? (
      <div className="meta-grid">
        {calculationTypeControl}
        {outputLevelControl}
      </div>
    ) : null
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault()
        if (validatingSave || busy) return
        setError('')
        setValidatingSave(true)
        try {
          const checked =
            formulaRequest && base ? await validateFormula(base, formulaRequest) : null
          if (d.measureType === 'calculated' && (!checked?.valid || !checked.expression)) {
            throw new Error(
              checked?.diagnostics.map((item) => item.message).join(' ') ||
                'Formula validation is unavailable.',
            )
          }
          const submittedKind = checked?.resultType?.type ?? resultKind
          const submittedNumeric = numericType(submittedKind)
          const submittedAggregation = aggregationForType(savedAggregation, submittedKind)
          const input =
            d.measureType === 'calculated'
              ? {
                  ...value,
                  definition: {
                    ...d,
                    resultType:
                      numeric && d.resultType.type === 'decimal'
                        ? d.resultType
                        : {
                            type: checked?.resultType?.type ?? resultKind,
                            nullable: d.resultType.nullable,
                          },
                    ...(d.calculationType === 'CalculateFirst'
                      ? {
                          aggregation: submittedAggregation,
                          allocation: submittedNumeric ? d.allocation : null,
                        }
                      : {}),
                    formula: {
                      displayText: text,
                      languageVersion: 'planning-v1' as const,
                      astVersion: 2 as const,
                      expression: checked!.expression!,
                    },
                  },
                }
              : {
                  ...value,
                  definition: {
                    ...d,
                    resultType: sourceResultType ?? { type: 'number' as const, nullable: true },
                    aggregation,
                    allocation: numeric ? d.allocation : null,
                    editable: numeric && d.editable,
                    missingTransactionValue: numeric
                      ? d.missingTransactionValue
                      : ('null' as const),
                  },
                }
          await onSave({
            ...input,
            business: {
              ...input.business,
              unit: !submittedNumeric
                ? null
                : input.definition.measureType === 'calculated'
                  ? (checked?.unit ??
                    formulaUnit(input.definition.formula.expression, data.measures) ??
                    value.business.unit)
                  : (valueColumn?.unit ?? value.business.unit),
              aliases: value.business.aliases,
            },
          })
        } catch (error) {
          setError(error instanceof Error ? error.message : 'Invalid formula.')
        } finally {
          setValidatingSave(false)
        }
      }}
    >
      {quick ? (
        <Text
          label="Name"
          value={value.name}
          onChange={(name) => set({ ...value, name, displayName: name })}
        />
      ) : (
        <IdentityFields value={value} editing={editing} onChange={(v) => set({ ...value, ...v })} />
      )}
      {!quick && (
        <div className="measure-definition-controls">
          <fieldset className="measure-type-control" disabled={busy}>
            <legend>
              Measure type <FieldHelp label="Measure type" />
            </legend>
            <div className="measure-type-switch">
              {(
                [
                  ['referenced', 'Reference'],
                  ['calculated', 'Calculated'],
                ] as const
              ).map(([type, label]) => (
                <label key={type} className="measure-type-option">
                  <input
                    type="radio"
                    name={typeGroupId}
                    value={type}
                    checked={d.measureType === type}
                    onChange={() => changeMeasureType(type)}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </fieldset>
          {calculationTypeControl}
          {d.measureType === 'calculated' && outputLevelControl}
        </div>
      )}
      {d.measureType === 'referenced' && (
        <>
          {schemaError && (
            <p className="meta-error" role="alert">
              {schemaError}
            </p>
          )}
          <div className="meta-grid">
            <Select
              label="Transaction"
              value={d.transactionId}
              items={options(data.transactions)}
              onChange={(transactionId) => {
                const transaction = data.transactions.find((t) => t.objectId === transactionId)!
                update({
                  transactionId,
                  nativePlanningLevelId: transaction.definition.planningLevelId,
                  valueColumnId: '',
                })
              }}
            />
            <Select
              label="Value column"
              value={d.valueColumnId}
              items={valueColumns
                .filter((c) => !!typedResultType(c.physicalType))
                .map((c) => ({ value: c.columnId, label: c.name }))}
              onChange={(valueColumnId) => update({ valueColumnId })}
            />
          </div>
          {outputLevelControl}
        </>
      )}
      {d.measureType === 'referenced' ? (
        <>
          {numeric && (
            <Check
              label="Editable measure"
              value={d.editable}
              onChange={(editable) => update({ editable })}
            />
          )}
          <div className="meta-grid">
            <Select
              label="Missing transaction value"
              value={d.missingTransactionValue}
              items={numeric ? ['null', 'zero'] : ['null']}
              onChange={(v) => update({ missingTransactionValue: v as 'null' | 'zero' })}
            />
          </div>
        </>
      ) : (
        <>
          {counted && (
            <p className="meta-hint">
              Counts evaluate inputs at Output Planning Level and recompute when viewed at a coarser
              grain. They count planning intersections, not raw transaction records.
            </p>
          )}
          <FormulaBuilder
            value={text}
            onChange={setText}
            measures={data.measures.filter((measure) => measure.objectId !== value.objectId)}
            attributes={attributes}
            functions={functions}
            data={data}
            validation={validation}
            attributeError={attributeError}
            functionError={functionError}
            levelSelected={!!d.nativePlanningLevelId}
            compact={quick}
            disabled={busy || validatingSave}
          />
          {base && d.nativePlanningLevelId && (
            <>
              <button
                type="button"
                className="secondary-button compatibility-open"
                aria-expanded={showCompatibility}
                onClick={() => setShowCompatibility(!showCompatibility)}
              >
                {showCompatibility ? 'Hide compatibility' : 'Explore measure compatibility'}
              </button>
              {showCompatibility && (
                <CompatibilityGraphPanel
                  base={base}
                  planningLevelId={d.nativePlanningLevelId}
                  measureIds={[...formulaInputs].slice(0, 12)}
                />
              )}
            </>
          )}
          {quick && (
            <button
              type="button"
              className="secondary-button quick-measure-advanced"
              aria-expanded={advanced}
              onClick={() => setAdvanced(!advanced)}
            >
              {advanced ? 'Hide advanced settings' : 'Advanced settings'}
            </button>
          )}
          {quick && showSettings && calculationSettings}
          {showSettings && (
            <p className="meta-hint">
              {d.calculationType === 'AggregateFirst'
                ? 'Inputs are aligned and aggregated before the formula is evaluated.'
                : 'The formula is evaluated at its native grain before its result is aggregated.'}
            </p>
          )}
        </>
      )}
      {!numeric && (
        <p className="meta-hint" role="status">
          Result: {resultKind === 'date' ? 'Date' : resultKind === 'boolean' ? 'Boolean' : 'String'}{' '}
          · Read only. Values are available at native and supported coarser grains; no finer-level
          distribution.
        </p>
      )}
      {showSettings && d.measureType === 'calculated' && (
        <>
          <Check
            label="Nullable result"
            value={d.resultType.nullable}
            onChange={(nullable) => update({ resultType: { ...d.resultType, nullable } })}
          />
        </>
      )}
      {showSettings &&
        (d.measureType === 'referenced' || d.calculationType === 'CalculateFirst') && (
          <>
            <h3>Aggregation policies</h3>
            <div className="meta-grid">
              <Select
                label={numeric ? 'Non-time aggregation' : 'Aggregation'}
                disabled={resultKind === 'string'}
                value={aggregation.nonTime}
                items={nonTimeOptions}
                onChange={(v) =>
                  update({
                    aggregation: {
                      ...aggregation,
                      nonTime: v as Aggregation['nonTime'],
                      ...(!numeric ? { time: v as Aggregation['time'] } : {}),
                    },
                  })
                }
              />
              {numeric && (
                <Select
                  label="Time aggregation"
                  value={aggregation.time}
                  items={timeOptions}
                  onChange={(v) =>
                    update({ aggregation: { ...aggregation, time: v as Aggregation['time'] } })
                  }
                />
              )}
              {numeric && (
                <Select
                  label="Aggregation order"
                  value={aggregation.order ?? 'nonTimeThenTime'}
                  items={[
                    { value: 'nonTimeThenTime', label: 'Non-time, then Time' },
                    { value: 'timeThenNonTime', label: 'Time, then non-time' },
                  ]}
                  onChange={(order) =>
                    update({
                      aggregation: { ...aggregation, order: order as Aggregation['order'] },
                    })
                  }
                />
              )}
              <Select
                label="Null input policy"
                value={aggregation.nullInputPolicy}
                items={['ignore', 'propagate']}
                onChange={(v) =>
                  update({
                    aggregation: { ...aggregation, nullInputPolicy: v as 'ignore' | 'propagate' },
                  })
                }
              />
              <Select
                label="All-null result"
                value={aggregation.allNullResult}
                items={numeric ? ['null', 'zero'] : ['null']}
                onChange={(v) =>
                  update({ aggregation: { ...aggregation, allNullResult: v as 'null' | 'zero' } })
                }
              />
            </div>
          </>
        )}
      {showSettings &&
        numeric &&
        (d.measureType === 'referenced' ||
          (d.calculationType === 'CalculateFirst' &&
            !(baseLevelId && d.nativePlanningLevelId === baseLevelId))) && (
          <>
            <h3>Dis-Aggregation policies</h3>
            <p className="meta-hint">
              Used for user edits. Materialisation splits SUM axes equally and repeats other axes.
            </p>
            <div className="meta-grid">
              <Select
                label="Dis-aggregation policy"
                value={d.allocation?.mode ?? 'none'}
                items={[
                  { value: 'none', label: 'None — repeat value' },
                  { value: 'equal', label: 'Equal' },
                  { value: 'weighted', label: 'Weighted' },
                  { value: 'weighted_by_self', label: 'Weighted by self' },
                ]}
                onChange={(v) =>
                  update({
                    allocation: {
                      mode: v as 'none' | 'equal' | 'weighted' | 'weighted_by_self',
                      ...(v === 'weighted' ? { basisMeasureId: '' } : {}),
                    },
                  })
                }
              />
              {d.allocation?.mode === 'weighted' && (
                <Select
                  label="Dis-aggregation basis measure"
                  value={d.allocation.basisMeasureId ?? ''}
                  items={options(data.measures.filter((m) => m.objectId !== value.objectId))}
                  onChange={(basisMeasureId) =>
                    update({ allocation: { mode: 'weighted', basisMeasureId } })
                  }
                />
              )}
            </div>

            {d.allocation?.mode === 'weighted_by_self' && (
              <p className="meta-hint">
                Uses this measure’s populated values at the edit write level (Base when reachable,
                otherwise its output level) as proportional weights.
              </p>
            )}
          </>
        )}
      {showSettings && numeric && !counted && (
        <MeasureBaseVisibility
          definition={d}
          levels={data.planningLevels}
          base={base}
          baseLevelId={baseLevelId}
        />
      )}
      {error && (
        <p className="meta-error" role="alert">
          {error}
        </p>
      )}
      <Actions busy={busy || validatingSave} onCancel={onCancel} />
    </form>
  )
}
