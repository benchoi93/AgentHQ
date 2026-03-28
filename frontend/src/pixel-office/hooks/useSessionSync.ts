import { useEffect, useRef, useState, useCallback } from 'react'
import { getSessions } from '../../api'
import type { Session } from '../../types'
import type { OfficeState } from '../engine/officeState'

interface UseSessionSyncOptions {
  officeState: OfficeState
  pollIntervalMs?: number
}

interface SessionSyncResult {
  sessions: Session[]
  /** Map from character ID to session */
  sessionByCharId: Map<number, Session>
  loading: boolean
  error: string | null
}

/**
 * Polls AgentHQ sessions and maps them to pixel office characters.
 * New sessions spawn characters; removed sessions despawn them.
 */
export function useSessionSync({
  officeState,
  pollIntervalMs = 3000,
}: UseSessionSyncOptions): SessionSyncResult {
  const [sessions, setSessions] = useState<Session[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Stable maps across renders
  const sessionToCharRef = useRef(new Map<string, number>())
  const charToSessionRef = useRef(new Map<number, Session>())
  const nextIdRef = useRef(1)
  const initializedRef = useRef(false)

  const syncSessions = useCallback(async () => {
    try {
      const data = await getSessions()
      setSessions(data)
      setError(null)

      const sessionToChar = sessionToCharRef.current
      const charToSession = charToSessionRef.current
      const isInitial = !initializedRef.current
      initializedRef.current = true

      // Only show running/idle/error/manual sessions as characters
      const activeSessions = data.filter(
        (s) => s.status === 'running' || s.status === 'idle' || s.status === 'error' || s.status === 'manual',
      )
      const activeIds = new Set(activeSessions.map((s) => s.id))

      // Remove characters for sessions that are gone
      for (const [sessionId, charId] of sessionToChar) {
        if (!activeIds.has(sessionId)) {
          officeState.removeAgent(charId)
          sessionToChar.delete(sessionId)
          charToSession.delete(charId)
        }
      }

      // Add/update characters for current sessions
      for (let i = 0; i < activeSessions.length; i++) {
        const session = activeSessions[i]

        if (!sessionToChar.has(session.id)) {
          // New session — spawn character
          const charId = nextIdRef.current++
          sessionToChar.set(session.id, charId)

          // Stagger spawns on initial load to avoid visual chaos
          const delay = isInitial ? i * 100 : 0
          if (delay > 0) {
            setTimeout(() => {
              officeState.addAgent(charId, undefined, undefined, undefined, false, session.project || session.id)
              applySessionState(officeState, charId, session)
              charToSession.set(charId, session)
            }, delay)
          } else {
            officeState.addAgent(charId, undefined, undefined, undefined, false, session.project || session.id)
            applySessionState(officeState, charId, session)
            charToSession.set(charId, session)
          }
        } else {
          // Existing session — update state
          const charId = sessionToChar.get(session.id)!
          applySessionState(officeState, charId, session)
          charToSession.set(charId, session)
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch sessions')
    } finally {
      setLoading(false)
    }
  }, [officeState, pollIntervalMs])

  useEffect(() => {
    syncSessions()
    const interval = setInterval(syncSessions, pollIntervalMs)
    return () => clearInterval(interval)
  }, [syncSessions, pollIntervalMs])

  return {
    sessions,
    sessionByCharId: charToSessionRef.current,
    loading,
    error,
  }
}

function applySessionState(officeState: OfficeState, charId: number, session: Session): void {
  const isActive = session.status === 'running' || session.status === 'error'
  officeState.setAgentActive(charId, isActive)

  // Show error bubble for error sessions
  if (session.status === 'error') {
    officeState.showPermissionBubble(charId)
  } else {
    officeState.clearPermissionBubble(charId)
  }

  // Update folder name for label display
  const ch = officeState.characters.get(charId)
  if (ch) {
    ch.folderName = session.project || session.id
  }
}
