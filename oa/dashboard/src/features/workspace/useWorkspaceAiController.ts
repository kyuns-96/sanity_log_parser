import { useCallback, useEffect, useRef, useState } from 'react'
import { selectedAiConnection, selectedAiModel } from '@/features/ai/AiModelSelect'
import { canRetryUnknown } from '@/features/ai/UnknownBatchRecovery'
import { aiJobActive } from './workspaceApi'
import type { CodexCapability } from './workspaceApi'

export interface UseWorkspaceAiControllerOptions {
  ai?: CodexCapability
  production: boolean
  baseKey: string
  pollEnabled: boolean
  pollIntervalMs: number
  pollTrigger: unknown
  onPoll: (signal: AbortSignal, epoch: number) => Promise<unknown>
  onTokenChanged: (signal: AbortSignal, epoch: number) => Promise<unknown>
  onError: (cause: unknown) => void
}

export function useWorkspaceAiController({
  ai,
  production,
  baseKey,
  pollEnabled,
  pollIntervalMs,
  pollTrigger,
  onPoll,
  onTokenChanged,
  onError,
}: UseWorkspaceAiControllerOptions) {
  const [selectedModel, setSelectedModel] = useState('')
  const [selectedConnection, setSelectedConnection] = useState('')
  const [pollPaused, setPollPaused] = useState(false)
  const polls = useRef({ id: '', count: 0 })
  const aiFetchEpoch = useRef(0)

  const callbacks = useRef({ onPoll, onTokenChanged, onError })
  callbacks.current = { onPoll, onTokenChanged, onError }

  const aiRunning = aiJobActive(ai?.job)
  const recoverableUnknown = canRetryUnknown(ai?.job)
  const connectionId = aiRunning
    ? ai!.job!.connection_id || ''
    : ai?.connections?.some(value => value.id === selectedConnection)
      ? selectedConnection
      : ai?.connection_id || ''
  const choice = selectedAiConnection(ai, connectionId)
  const modelId = aiRunning ? ai!.job!.model : selectedAiModel(choice, selectedModel)

  const chooseConnection = useCallback((value: string) => {
    setSelectedConnection(value)
    setSelectedModel('')
  }, [])

  const bumpEpoch = useCallback(() => {
    aiFetchEpoch.current += 1
    return aiFetchEpoch.current
  }, [])

  const resetPoll = useCallback((jobId?: string) => {
    if (jobId) polls.current = { id: jobId, count: 0 }
    else polls.current.count = 0
    setPollPaused(false)
  }, [])

  useEffect(() => {
    if (!production) return
    let pending: AbortController | undefined
    const changed = () => {
      pending?.abort()
      const controller = new AbortController()
      pending = controller
      const epoch = aiFetchEpoch.current
      callbacks.current.onTokenChanged(controller.signal, epoch).catch(callbacks.current.onError)
    }
    window.addEventListener('sanity-ai-token-changed', changed)
    return () => {
      pending?.abort()
      window.removeEventListener('sanity-ai-token-changed', changed)
    }
  }, [baseKey, production])

  const activeJobId = ai?.job?.id || ''
  useEffect(() => {
    if (!pollEnabled || !aiRunning || pollPaused || !activeJobId) return
    if (polls.current.id !== activeJobId) polls.current = { id: activeJobId, count: 0 }
    if (polls.current.count >= 60) {
      setPollPaused(true)
      return
    }
    const controller = new AbortController()
    const timer = setTimeout(() => {
      polls.current.count += 1
      callbacks.current.onPoll(controller.signal, aiFetchEpoch.current).catch(callbacks.current.onError)
    }, pollIntervalMs)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [baseKey, pollEnabled, aiRunning, activeJobId, pollTrigger, pollPaused, pollIntervalMs])

  return {
    selectedModel,
    setSelectedModel,
    selectedConnection,
    chooseConnection,
    pollPaused,
    setPollPaused,
    aiFetchEpoch,
    bumpEpoch,
    resetPoll,
    aiRunning,
    recoverableUnknown,
    connectionId,
    choice,
    modelId,
  }
}
