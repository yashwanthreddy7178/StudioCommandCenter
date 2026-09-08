// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useRunStream } from './useRunStream';
import { EventType, StepEvent } from '../types/api';

/** Stand-in for the browser's EventSource, which jsdom does not implement.
 *
 * Records every instance so a test can push frames into the stream the hook is
 * actually listening to, and tracks close() so the reconnect behaviour on a run
 * change is observable.
 */
class FakeEventSource {
  static instances: FakeEventSource[] = [];

  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  closed = false;

  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  /** Delivers one server frame to the hook. */
  emit(event: Partial<StepEvent>) {
    act(() => {
      this.onmessage?.({ data: JSON.stringify(event) } as MessageEvent);
    });
  }
}

const event = (seq: number, type: EventType, extra: Record<string, unknown> = {}) => ({
  seq,
  run_id: 'r',
  tenant_id: 't01',
  event_type: type,
  title: `event ${seq}`,
  description: `description ${seq}`,
  ...extra,
});

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  // The hook appends the operator token to the stream URL.
  vi.stubGlobal('localStorage', {
    getItem: () => null,
    setItem: () => undefined,
    removeItem: () => undefined,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useRunStream', () => {
  it('clears the previous run when a second investigation starts', async () => {
    // The reported bug: simulate an incident, investigate, approve, then
    // simulate and investigate again -- the ledger and scorecard stayed on the
    // first run's results.
    const { result, rerender } = renderHook(({ runId }) => useRunStream(runId), {
      initialProps: { runId: 'run-first' as string | null },
    });

    const first = FakeEventSource.instances[0];
    first.emit(event(1, 'PLAN'));
    first.emit(event(2, 'HYPOTHESIS', { payload: { confidence: 'HIGH', passed_count: 6 } }));
    first.emit(event(3, 'IMPACT', { payload: { delay_minutes: 47 } }));

    await waitFor(() => expect(result.current.events).toHaveLength(3));
    expect(result.current.hypothesis).not.toBeNull();
    expect(result.current.impact).not.toBeNull();

    // A second run begins.
    rerender({ runId: 'run-second' });

    await waitFor(() => expect(result.current.events).toHaveLength(0));
    expect(result.current.hypothesis).toBeNull();
    expect(result.current.impact).toBeNull();
    expect(result.current.options).toEqual([]);
    expect(result.current.verificationImpact).toBeNull();
    expect(result.current.runState).toBe('QUEUED');
    // The first stream is not left open.
    expect(first.closed).toBe(true);
  });

  it('accepts the new run’s events even though they reuse the same sequence numbers', async () => {
    // Each run numbers its events from 1. With the old run's events still in
    // state, the duplicate check dropped the new run's entirely, so the panels
    // sat frozen rather than merely showing stale data for a moment.
    const { result, rerender } = renderHook(({ runId }) => useRunStream(runId), {
      initialProps: { runId: 'run-first' as string | null },
    });

    FakeEventSource.instances[0].emit(event(1, 'PLAN', { title: 'first run step' }));
    await waitFor(() => expect(result.current.events).toHaveLength(1));

    rerender({ runId: 'run-second' });
    const second = FakeEventSource.instances[1];
    second.emit(event(1, 'PLAN', { title: 'second run step' }));

    await waitFor(() => expect(result.current.events).toHaveLength(1));
    expect(result.current.events[0].title).toBe('second run step');
    expect(result.current.runState).toBe('RUNNING');
  });

  it('still drops a genuine duplicate within one run', async () => {
    // Reconnects replay from the last event id, so the same frame can arrive
    // twice inside a single run.
    const { result } = renderHook(() => useRunStream('run-first'));
    const stream = FakeEventSource.instances[0];

    stream.emit(event(1, 'PLAN'));
    stream.emit(event(1, 'PLAN'));
    stream.emit(event(2, 'EVIDENCE'));

    await waitFor(() => expect(result.current.events).toHaveLength(2));
  });

  it('clears everything when the run goes away', async () => {
    const { result, rerender } = renderHook(({ runId }) => useRunStream(runId), {
      initialProps: { runId: 'run-first' as string | null },
    });

    FakeEventSource.instances[0].emit(event(1, 'PLAN'));
    await waitFor(() => expect(result.current.events).toHaveLength(1));

    rerender({ runId: null });

    await waitFor(() => expect(result.current.events).toHaveLength(0));
    expect(result.current.isStreaming).toBe(false);
  });
});

describe('run completion', () => {
  it('stops streaming when the run ends after verification', async () => {
    // VERIFICATION used to close the stream server-side while the client kept
    // waiting for a COMPLETED that never came, so the launch button stayed on
    // "Investigating" for the rest of the session. The run now ends itself.
    const { result } = renderHook(() => useRunStream('run-verified'));
    const stream = FakeEventSource.instances[0];

    stream.emit(event(1, 'PLAN'));
    await waitFor(() => expect(result.current.isStreaming).toBe(true));

    stream.emit(event(2, 'VERIFICATION', {
      payload: { verification_impact: { is_remediated: true }, status: 'VERIFIED' },
    }));

    // Verification alone is not an ending: more events follow it.
    await waitFor(() => expect(result.current.runState).toBe('VERIFYING'));
    expect(result.current.isStreaming).toBe(true);
    expect(result.current.verificationImpact).not.toBeNull();

    stream.emit(event(3, 'COMPLETED', { payload: { is_recovered: true } }));

    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    expect(result.current.runState).toBe('COMPLETED');
  });

  it('also stops streaming when the fleet did not recover', async () => {
    // The unhappy path ends too. DEGRADED cannot carry the ending -- it fires
    // for a single failed tool call mid-run -- so COMPLETED is emitted either
    // way and the recovery outcome rides in the payload.
    const { result } = renderHook(() => useRunStream('run-partial'));
    const stream = FakeEventSource.instances[0];

    stream.emit(event(1, 'PLAN'));
    stream.emit(event(2, 'VERIFICATION', {
      payload: {
        verification_impact: { is_remediated: false },
        status: 'PARTIALLY_RECOVERED',
      },
    }));
    stream.emit(event(3, 'COMPLETED', { payload: { is_recovered: false } }));

    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    expect(result.current.verificationImpact?.is_remediated).toBe(false);
  });
});
