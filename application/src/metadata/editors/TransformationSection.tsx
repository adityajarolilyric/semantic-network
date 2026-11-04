import type { ReactNode } from 'react'

export function TransformationSection({
  title,
  configured,
  status,
  children,
}: {
  title: string
  configured: boolean
  status?: string
  children: ReactNode
}) {
  return (
    <details className="transformation-section">
      <summary>
        <span>{title}</span>
        <span className={configured ? 'badge' : 'meta-hint'}>
          {status ?? (configured ? 'Enabled' : 'Optional')}
        </span>
      </summary>
      <div className="transformation-section-body">{children}</div>
    </details>
  )
}
