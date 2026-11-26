import {useCallback, useEffect, useRef, useState} from 'react';
import {api} from './api';
import {confluenceProfile, type ConfluenceReceiptCursor, type ConfluenceReceiptPage} from './replayConfluenceTypes';

const changeEvent = 'tradeforge.replay-confluence-saved';

export function announceSavedConfluence(sessionId: string) {
  if (typeof BroadcastChannel === 'undefined') {
    window.dispatchEvent(new CustomEvent(changeEvent, {detail: sessionId}));
    return;
  }
  const channel = new BroadcastChannel(changeEvent);
  channel.postMessage(sessionId);
  channel.close();
}

export function useSavedConfluenceReceipts(sessionId: string, cutoff: number, restoredSelection?: string) {
  const [request, requestPage] = useState<{cutoff: number; cursor: ConfluenceReceiptCursor}>();
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{cutoff: number; page?: ConfluenceReceiptPage; loading: boolean; error?: string}>();
  const generation = useRef(0);
  const cursor = request?.cutoff === cutoff ? request.cursor : undefined;

  // Start a fresh page sequence after a chart clock change, including when
  // the operator later returns to a cutoff they previously paginated.
  useEffect(() => {requestPage(undefined);}, [sessionId, cutoff]);

  const refresh = useCallback(() => {
    generation.current++;
    requestPage(undefined);
    setRevision(value => value + 1);
  }, []);

  useEffect(() => {
    const channel = typeof BroadcastChannel === 'undefined' ? undefined : new BroadcastChannel(changeEvent);
    const receive = (id: unknown) => {if (id === sessionId) refresh();};
    const receiveLocal = (event: Event) => receive((event as CustomEvent).detail);
    const receiveRemote = (event: MessageEvent) => receive(event.data);
    channel?.addEventListener('message', receiveRemote);
    window.addEventListener(changeEvent, receiveLocal);
    window.addEventListener('focus', refresh);
    return () => {
      channel?.close();
      window.removeEventListener(changeEvent, receiveLocal);
      window.removeEventListener('focus', refresh);
    };
  }, [sessionId, refresh]);

  useEffect(() => {
    let active = true;
    const requestGeneration = generation.current;
    const query = new URLSearchParams({cutoff: String(cutoff), profile: confluenceProfile});
    if (cursor) {
      query.set('before_cutoff', String(cursor.cutoff));
      query.set('before_id', cursor.receipt_id);
    } else if (restoredSelection) {
      query.set('selected_id', restoredSelection);
    }
    const path = '/replay/sessions/' + encodeURIComponent(sessionId) + '/confluence/receipts?' + query;
    setResult(previous => ({cutoff, page: previous?.cutoff === cutoff ? previous.page : undefined, loading: true}));
    api.get<ConfluenceReceiptPage>(path).then(page => {
      if (!active || requestGeneration !== generation.current) return;
      setResult(previous => {
        if (!cursor || previous?.cutoff !== cutoff || !previous.page) return {cutoff, page, loading: false};
        return {cutoff, loading: false, page: {
          ...page,
          selected: previous.page.selected,
          receipts: [...previous.page.receipts, ...page.receipts],
        }};
      });
    }).catch(reason => {
      if (active && requestGeneration === generation.current) setResult(previous => ({
        cutoff, page: previous?.cutoff === cutoff ? previous.page : undefined,
        loading: false, error: reason instanceof Error ? reason.message : String(reason),
      }));
    });
    return () => {active = false;};
  }, [sessionId, cutoff, cursor, restoredSelection, revision]);

  const current = result?.cutoff === cutoff ? result : undefined;
  const next = current?.page?.next_cursor;
  return {
    page: current?.page,
    loading: current?.loading ?? true,
    error: current?.error,
    refresh,
    loadOlder: next ? () => {
      generation.current++;
      requestPage({cutoff, cursor: {...next}});
    } : undefined,
  };
}
