import { useId, useRef, useState, type ReactNode } from 'react'

type TransformationTab = { label: string; configured: boolean; content: ReactNode }
export function TransformationsPanel({
  tabs,
  label = 'Transformations',
}: {
  tabs: TransformationTab[]
  label?: string
}) {
  const [active, setActive] = useState(0)
  const panel = useRef<HTMLDetailsElement>(null)
  const tabButtons = useRef<(HTMLButtonElement | null)[]>([])
  const id = useId()
  const enabledCount = tabs.filter((tab) => tab.configured).length
  return (
    <details ref={panel} className="transformation-section transaction-transformations">
      <summary>
        <span>Transformations</span>
        <span className={enabledCount ? 'badge' : 'meta-hint'}>
          {enabledCount ? `${enabledCount} configured` : 'Optional'}
        </span>
      </summary>
      <div className="transformation-section-body">
        <div className="transaction-transform-tabs" role="tablist" aria-label={label}>
          {tabs.map((tab, index) => (
            <button
              key={tab.label}
              type="button"
              role="tab"
              id={`${id}-tab-${index}`}
              aria-controls={`${id}-panel-${index}`}
              aria-selected={active === index}
              tabIndex={active === index ? 0 : -1}
              ref={(element) => {
                tabButtons.current[index] = element
              }}
              onClick={() => setActive(index)}
              onKeyDown={(event) => {
                const next =
                  event.key === 'ArrowRight'
                    ? (index + 1) % tabs.length
                    : event.key === 'ArrowLeft'
                      ? (index + tabs.length - 1) % tabs.length
                      : event.key === 'Home'
                        ? 0
                        : event.key === 'End'
                          ? tabs.length - 1
                          : null
                if (next === null) return
                event.preventDefault()
                setActive(next)
                tabButtons.current[next]?.focus()
              }}
            >
              {tab.label}
              {tab.configured && (
                <span className="transaction-transform-dot" aria-hidden="true" title="Configured" />
              )}
            </button>
          ))}
        </div>
        {tabs.map((tab, index) => (
          <div
            key={tab.label}
            role="tabpanel"
            id={`${id}-panel-${index}`}
            aria-labelledby={`${id}-tab-${index}`}
            hidden={active !== index}
            onInvalidCapture={() => {
              if (panel.current) panel.current.open = true
              setActive(index)
            }}
          >
            {tab.content}
          </div>
        ))}
      </div>
    </details>
  )
}
