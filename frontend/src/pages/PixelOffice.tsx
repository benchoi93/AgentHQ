import { useRef, useState, useEffect, useCallback } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { ArrowLeft, Users } from 'lucide-react'
import { OfficeState } from '../pixel-office/engine/officeState'
import { loadAllSprites } from '../pixel-office/sprites/assetLoader'
import { useSessionSync } from '../pixel-office/hooks/useSessionSync'
import { OfficeCanvas } from '../pixel-office/components/OfficeCanvas'
import { AgentLabels } from '../pixel-office/components/AgentLabels'
import { ZOOM_DEFAULT_DPR_FACTOR } from '../pixel-office/constants'

// Singleton OfficeState so it persists across re-renders
let _officeState: OfficeState | null = null
function getOfficeState(): OfficeState {
  if (!_officeState) {
    _officeState = new OfficeState()
  }
  return _officeState
}

export default function PixelOffice() {
  const officeState = getOfficeState()
  const navigate = useNavigate()
  const containerRef = useRef<HTMLDivElement>(null)
  const panRef = useRef({ x: 0, y: 0 })

  const [zoom, setZoom] = useState(() => {
    const dpr = window.devicePixelRatio || 1
    return Math.round(dpr * ZOOM_DEFAULT_DPR_FACTOR)
  })

  const [spritesLoaded, setSpritesLoaded] = useState(false)

  useEffect(() => {
    loadAllSprites().then(() => setSpritesLoaded(true))
  }, [])

  const { sessions, sessionByCharId } = useSessionSync({
    officeState,
    pollIntervalMs: 3000,
  })

  const handleCharacterClick = useCallback(
    (charId: number) => {
      const session = sessionByCharId.get(charId)
      if (session) {
        navigate(`/session/${session.id}`)
      }
    },
    [sessionByCharId, navigate],
  )

  const activeCount = sessions.filter(
    (s) => s.status === 'running' || s.status === 'idle' || s.status === 'error',
  ).length

  return (
    <div className="h-screen flex flex-col bg-slate-950">
      {/* Header */}
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur-sm z-10 shrink-0">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-12">
            <div className="flex items-center gap-3">
              <Link
                to="/"
                className="p-1.5 text-slate-400 hover:text-slate-200 rounded-lg hover:bg-slate-800 transition-colors"
                title="Back to Dashboard"
              >
                <ArrowLeft className="w-4 h-4" />
              </Link>
              <h1 className="text-sm font-semibold text-slate-100">Pixel Office</h1>
              <div className="flex items-center gap-1.5 text-xs text-slate-500">
                <Users className="w-3 h-3" />
                <span>
                  {activeCount} agent{activeCount !== 1 ? 's' : ''}
                </span>
              </div>
            </div>
            <div className="text-xs text-slate-600">
              Scroll to pan | Ctrl+scroll to zoom | Click agent to view session
            </div>
          </div>
        </div>
      </header>

      {/* Canvas area */}
      <div ref={containerRef} className="flex-1 relative overflow-hidden" style={{ background: '#1E1E2E' }}>
        {spritesLoaded && (
          <>
            <OfficeCanvas
              officeState={officeState}
              zoom={zoom}
              onZoomChange={setZoom}
              panRef={panRef}
              onCharacterClick={handleCharacterClick}
              containerRef={containerRef}
            />
            <AgentLabels
              officeState={officeState}
              sessionByCharId={sessionByCharId}
              containerRef={containerRef}
              zoom={zoom}
              panRef={panRef}
            />
          </>
        )}

        {!spritesLoaded && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-slate-500 text-sm">Loading pixel office...</span>
          </div>
        )}

        {/* Subtle vignette */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            background: 'radial-gradient(ellipse at center, transparent 60%, rgba(0,0,0,0.3) 100%)',
          }}
        />
      </div>
    </div>
  )
}
