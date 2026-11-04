import { useLayoutEffect, type RefObject } from 'react'

/** Use real header widths so row spans and variable-width labels stay aligned. */
export function usePinnedPivotColumns(
  table: RefObject<HTMLTableElement | null>,
  count: number,
  layout: unknown,
) {
  useLayoutEffect(() => {
    const element = table.current
    const headers = element?.tHead?.rows[0]
    if (!element || !headers) return
    const pinned = Array.from(headers.cells).slice(0, count)
    const align = () => {
      let left = 0
      pinned.forEach((header, index) => {
        element.querySelectorAll<HTMLElement>(`[data-pinned-column="${index}"]`).forEach((cell) => {
          cell.style.left = `${left}px`
        })
        left += header.getBoundingClientRect().width
      })
    }
    align()
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(align)
      pinned.forEach((header) => observer.observe(header))
      return () => observer.disconnect()
    }
    window.addEventListener('resize', align)
    return () => window.removeEventListener('resize', align)
  }, [table, count, layout])
}
