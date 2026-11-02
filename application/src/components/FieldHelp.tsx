import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Info } from 'lucide-react'
import { fieldHelp } from '../metadata/fieldHelp'

/** Local help only. Kept outside labels so it never changes a field's name.
 * The keyboard-enabled span also works inside disabled/read-only form fieldsets.
 */
export function FieldHelp({ label, topic = label }: { label: string; topic?: string }) {
  const content = fieldHelp(topic)
  const id = useId()
  const button = useRef<HTMLSpanElement>(null)
  const popup = useRef<HTMLDivElement>(null)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const reveal = () => {
    clearTimeout(closeTimer.current)
    setOpen(true)
  }
  const leave = () => {
    clearTimeout(closeTimer.current)
    if (document.activeElement !== button.current)
      closeTimer.current = setTimeout(() => setOpen(false), 150)
  }
  useEffect(() => () => clearTimeout(closeTimer.current), [])
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState({ left: 0, top: 0 })
  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      const anchor = button.current?.getBoundingClientRect()
      const box = popup.current?.getBoundingClientRect()
      if (!anchor || !box) return
      setPosition({
        left: Math.max(12, Math.min(anchor.left, window.innerWidth - box.width - 12)),
        top:
          anchor.bottom + box.height + 20 <= window.innerHeight
            ? anchor.bottom + 8
            : Math.max(12, anchor.top - box.height - 8),
      })
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [open])
  useEffect(() => {
    if (!open) return
    const dismiss = (event: PointerEvent) => {
      if (
        !button.current?.contains(event.target as Node) &&
        !popup.current?.contains(event.target as Node)
      )
        setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      document.removeEventListener('keydown', escape)
    }
  }, [open])
  if (!content) return null
  return (
    <>
      <span
        ref={button}
        role="button"
        tabIndex={0}
        className="field-info"
        aria-label={'About ' + label}
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            event.stopPropagation()
            reveal()
          }
        }}
        onMouseEnter={reveal}
        onMouseLeave={leave}
        onFocus={reveal}
        onBlur={() => setOpen(false)}
        onClick={(event) => {
          event.preventDefault()
          event.stopPropagation()
          reveal()
        }}
      >
        <Info size={14} aria-hidden="true" />
      </span>
      {open &&
        createPortal(
          <div
            ref={popup}
            id={id}
            role="tooltip"
            className="field-info-popup"
            style={position}
            onMouseEnter={reveal}
            onMouseLeave={leave}
          >
            <strong>{label}</strong>
            <p>{content}</p>
          </div>,
          document.body,
        )}
    </>
  )
}
