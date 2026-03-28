import { useState, useEffect } from 'react'
import type { OfficeState } from '../engine/officeState'
import type { Session } from '../../types'
import { TILE_SIZE, CharacterState } from '../types'

interface AgentLabelsProps {
  officeState: OfficeState
  sessionByCharId: Map<number, Session>
  containerRef: React.RefObject<HTMLDivElement | null>
  zoom: number
  panRef: React.RefObject<{ x: number; y: number }>
}

export function AgentLabels({
  officeState,
  sessionByCharId,
  containerRef,
  zoom,
  panRef,
}: AgentLabelsProps) {
  const [, setTick] = useState(0)
  useEffect(() => {
    let rafId = 0
    const tick = () => {
      setTick((n) => n + 1)
      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafId)
  }, [])

  const el = containerRef.current
  if (!el) return null
  const rect = el.getBoundingClientRect()
  const dpr = window.devicePixelRatio || 1
  const canvasW = Math.round(rect.width * dpr)
  const canvasH = Math.round(rect.height * dpr)
  const layout = officeState.getLayout()
  const mapW = layout.cols * TILE_SIZE * zoom
  const mapH = layout.rows * TILE_SIZE * zoom
  const pan = panRef.current ?? { x: 0, y: 0 }
  const deviceOffsetX = Math.floor((canvasW - mapW) / 2) + Math.round(pan.x)
  const deviceOffsetY = Math.floor((canvasH - mapH) / 2) + Math.round(pan.y)

  const charIds = Array.from(officeState.characters.keys())

  return (
    <>
      {charIds.map((id) => {
        const ch = officeState.characters.get(id)
        if (!ch) return null
        // Don't show labels for despawning characters
        if (ch.matrixEffect === 'despawn') return null

        const sittingOffset = ch.state === CharacterState.TYPE ? 6 : 0
        const screenX = (deviceOffsetX + ch.x * zoom) / dpr
        const screenY = (deviceOffsetY + (ch.y + sittingOffset - 24) * zoom) / dpr

        const session = sessionByCharId.get(id)
        const isActive = ch.isActive
        const isError = session?.status === 'error'

        let dotColor = 'transparent'
        if (isError) {
          dotColor = '#ef4444' // red
        } else if (isActive) {
          dotColor = '#22c55e' // green
        } else {
          dotColor = '#eab308' // yellow for idle
        }

        const labelText = ch.folderName || session?.project || `Agent #${id}`

        return (
          <div
            key={id}
            style={{
              position: 'absolute',
              left: screenX,
              top: screenY - 16,
              transform: 'translateX(-50%)',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              pointerEvents: 'none',
              zIndex: 40,
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: dotColor,
                marginBottom: 2,
              }}
            />
            <span
              style={{
                fontFamily: 'monospace',
                fontSize: '11px',
                color: '#e2e8f0',
                background: 'rgba(15, 23, 42, 0.8)',
                padding: '1px 5px',
                borderRadius: 3,
                whiteSpace: 'nowrap',
                maxWidth: 140,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {labelText}
            </span>
          </div>
        )
      })}
    </>
  )
}
