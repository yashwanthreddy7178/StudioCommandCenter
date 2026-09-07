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
