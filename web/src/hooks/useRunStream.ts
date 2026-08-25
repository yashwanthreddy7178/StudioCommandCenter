import { useState, useEffect, useRef } from 'react';
import {
  StepEvent,
  RunState,
  HypothesisScorecard,
  ImpactProjection,
  RemediationOption,
} from '../types/api';

export function useRunStream(runId: string | null) {
  const [events, setEvents] = useState<StepEvent[]>([]);
  const [runState, setRunState] = useState<RunState>('QUEUED');
  const [hypothesis, setHypothesis] = useState<HypothesisScorecard | null>(null);
  const [impact, setImpact] = useState<ImpactProjection | null>(null);
  const [options, setOptions] = useState<RemediationOption[]>([]);
  const [verificationImpact, setVerificationImpact] = useState<ImpactProjection | null>(null);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const lastSeqRef = useRef<number>(0);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!runId) {
      setEvents([]);
      setRunState('QUEUED');
      setHypothesis(null);
      setImpact(null);
      setOptions([]);
      setVerificationImpact(null);
      return;
    }

    setIsStreaming(true);
    setError(null);
    lastSeqRef.current = 0;

    const url = `/api/stream/runs/${runId}/events?since_seq=${lastSeqRef.current}`;
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.onmessage = (e) => {
      try {
        const event: StepEvent = JSON.parse(e.data);
        if (event.seq) {
          lastSeqRef.current = Math.max(lastSeqRef.current, event.seq);
        }

        setEvents((prev) => {
          if (prev.some((p) => p.seq === event.seq)) return prev;
          return [...prev, event];
        });

        // Event reducer
        switch (event.event_type) {
          case 'PLAN':
            setRunState('RUNNING');
            break;
          case 'HYPOTHESIS':
            setHypothesis(event.payload as HypothesisScorecard);
            break;
          case 'IMPACT':
            setImpact(event.payload as ImpactProjection);
            break;
          case 'APPROVAL_REQUIRED':
            setRunState('AWAITING_APPROVAL');
            if (event.payload?.options) {
              setOptions(event.payload.options);
            }
            break;
          case 'VERIFICATION':
            setRunState('VERIFYING');
            if (event.payload?.verification_impact) {
              setVerificationImpact(event.payload.verification_impact);
            }
            break;
          case 'COMPLETED':
            setRunState('COMPLETED');
            setIsStreaming(false);
            break;
          case 'DEGRADED':
            setRunState('DEGRADED');
            break;
          case 'ERROR':
            setRunState('FAILED');
            setError(event.description);
            setIsStreaming(false);
            break;
        }
      } catch (err) {
        console.warn('Failed to parse SSE event data', err);
      }
    };

    es.onerror = (err) => {
      console.warn('SSE stream dropped or reconnecting', err);
    };

    return () => {
      es.close();
      setIsStreaming(false);
    };
  }, [runId]);

  return {
    events,
    runState,
    hypothesis,
    impact,
    options,
    verificationImpact,
    isStreaming,
    error,
  };
}
