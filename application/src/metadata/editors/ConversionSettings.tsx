import type { Measure, RefDefinition } from '../types'
import { Check, Select, Text } from './Fields'

export function ConversionSettings({
  value,
  measures,
  ownerId,
  onChange,
}: {
  value: RefDefinition
  measures: Measure[]
  ownerId: string
  onChange: (patch: Partial<RefDefinition>) => void
}) {
  return (
    <details className="meta-subpanel" open={!!value.conversion || !!value.isConversionFactor}>
      <summary>Units and currency</summary>
      <Check
        label="This measure is a conversion factor"
        value={!!value.isConversionFactor}
        onChange={(isConversionFactor) =>
          onChange({
            isConversionFactor,
            conversion: null,
            editable: false,
            allocation: { mode: 'none' },
            aggregation: isConversionFactor
              ? {
                  nonTime: 'single_value',
                  time: 'single_value',
                  nullInputPolicy: 'ignore',
                  allNullResult: 'null',
                }
              : { nonTime: 'sum', time: 'sum', nullInputPolicy: 'ignore', allNullResult: 'null' },
          })
        }
      />
      {value.isConversionFactor ? (
        <p className="meta-hint">
          Load this column from your conversion-table Transaction. Factors are positive, read only
          and repeat at finer grains. Conflicting factors are never summed or averaged. Use this
          stored measure in a formula such as [Qty] * [Factor].
        </p>
      ) : (
        <>
          <Check
            label="Apply a conversion"
            value={!!value.conversion}
            onChange={(enabled) =>
              onChange({
                conversion: enabled
                  ? { factorMeasureId: '', operation: 'multiply', targetUnit: '' }
                  : null,
              })
            }
          />
          {value.conversion && (
            <div className="meta-grid">
              <Select
                label="Conversion factor"
                value={value.conversion.factorMeasureId}
                items={measures
                  .filter(
                    (measure) =>
                      measure.objectId !== ownerId &&
                      measure.definition.measureType === 'referenced' &&
                      measure.definition.isConversionFactor,
                  )
                  .map((measure) => ({ value: measure.objectId, label: measure.name }))}
                onChange={(factorMeasureId) =>
                  onChange({ conversion: { ...value.conversion!, factorMeasureId } })
                }
              />
              <Select
                label="Conversion operation"
                value={value.conversion.operation}
                items={[
                  { value: 'multiply', label: 'Multiply by factor' },
                  { value: 'divide', label: 'Divide by factor' },
                ]}
                onChange={(operation) =>
                  onChange({
                    conversion: {
                      ...value.conversion!,
                      operation: operation as 'multiply' | 'divide',
                    },
                  })
                }
              />
              <Text
                label="Target unit or currency"
                value={value.conversion.targetUnit}
                onChange={(targetUnit) =>
                  onChange({ conversion: { ...value.conversion!, targetUnit } })
                }
              />
            </div>
          )}
          {value.conversion && (
            <p className="meta-hint">
              Conversion changes the stored measure value; source data is preserved. The factor must
              be determined at this measure’s native grain. For daily rates, convert daily values
              before rolling up to weeks.
            </p>
          )}
        </>
      )}
    </details>
  )
}
