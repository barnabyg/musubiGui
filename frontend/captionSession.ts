import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react';
import { apiClient } from './api';
import type { DatasetItem } from './Datasets';

const saveCaption = apiClient(
  'Caption save failed. Retry when access is restored.',
);

export type Caption = DatasetItem['caption'];
type Snapshot = {
  text: string;
  caption: Caption;
  status: string;
  error: string;
  saving: boolean;
};
type Entry = {
  item: DatasetItem;
  snapshot: Snapshot;
  timer?: number;
  pending?: Promise<boolean>;
};
type Summary = { unsaved: number; saving: number; failed: number };

// Dataset-owned drafts outlive virtual cards. Both card and preview subscribe to
// the same revision and write queue, so they cannot race independent saves.
export class CaptionSession {
  private entries = new Map<string, Entry>();
  private listeners = new Map<string, Set<() => void>>();
  private summaryListeners = new Set<() => void>();
  private summary: Summary = { unsaved: 0, saving: 0, failed: 0 };

  constructor(
    readonly datasetId: string,
    private onSaved: (id: string, caption: Caption) => void,
  ) {}

  private entry(item: DatasetItem): Entry {
    let entry = this.entries.get(item.id);
    if (!entry) {
      entry = {
        item,
        snapshot: {
          text: item.caption.text,
          caption: item.caption,
          status: item.caption.error ?? 'Caption loaded',
          error: '',
          saving: false,
        },
      };
      this.entries.set(item.id, entry);
    }
    return entry;
  }

  get(item: DatasetItem): Snapshot {
    return this.entry(item).snapshot;
  }
  getSummary = (): Summary => this.summary;
  subscribeSummary = (listener: () => void) => {
    this.summaryListeners.add(listener);
    return () => {
      this.summaryListeners.delete(listener);
    };
  };
  subscribe(id: string, listener: () => void) {
    const listeners = this.listeners.get(id) ?? new Set();
    this.listeners.set(id, listeners);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
      if (!listeners.size) this.listeners.delete(id);
    };
  }
  private publish(entry: Entry, patch: Partial<Snapshot>) {
    entry.snapshot = { ...entry.snapshot, ...patch };
    this.listeners.get(entry.item.id)?.forEach((listener) => listener());
    const summary = { unsaved: 0, saving: 0, failed: 0 };
    for (const value of this.entries.values()) {
      summary.unsaved += Number(
        Boolean(value.pending) ||
          value.snapshot.saving ||
          Boolean(value.snapshot.error) ||
          value.snapshot.text !== value.snapshot.caption.text,
      );
      summary.saving += Number(value.snapshot.saving);
      summary.failed += Number(Boolean(value.snapshot.error));
    }
    if (
      summary.unsaved !== this.summary.unsaved ||
      summary.saving !== this.summary.saving ||
      summary.failed !== this.summary.failed
    ) {
      this.summary = summary;
      this.summaryListeners.forEach((listener) => listener());
    }
  }
  sync(items: DatasetItem[]) {
    for (const item of items) {
      const entry = this.entries.get(item.id);
      if (
        !entry ||
        entry.pending ||
        entry.snapshot.text !== entry.snapshot.caption.text ||
        entry.snapshot.error
      )
        continue;
      if (
        entry.snapshot.caption.revision !== item.caption.revision ||
        entry.snapshot.caption.error !== item.caption.error
      ) {
        this.publish(entry, {
          text: item.caption.text,
          caption: item.caption,
          status: item.caption.error ?? 'Caption loaded',
        });
      }
    }
  }
  change(item: DatasetItem, text: string) {
    const entry = this.entry(item);
    window.clearTimeout(entry.timer);
    this.publish(entry, {
      text,
      status: entry.snapshot.saving ? 'Saving…' : 'Unsaved changes',
    });
    entry.timer = window.setTimeout(() => {
      void this.flush(item.id);
    }, 350);
  }
  async flush(id: string): Promise<boolean> {
    const entry = this.entries.get(id);
    if (!entry) return true;
    window.clearTimeout(entry.timer);
    for (;;) {
      if (entry.pending) {
        if (!(await entry.pending)) return false;
        continue;
      }
      if (
        entry.snapshot.text === entry.snapshot.caption.text &&
        !entry.snapshot.error
      )
        return true;
      entry.pending = this.write(entry);
      if (!(await entry.pending)) return false;
      // Include edits made while the request was in flight before allowing navigation.
    }
  }
  async restore(item: DatasetItem, historyId: string): Promise<boolean> {
    const entry = this.entry(item);
    do {
      if (!(await this.flush(item.id))) return false;
    } while (
      entry.pending ||
      entry.snapshot.text !== entry.snapshot.caption.text
    );
    entry.pending = this.write(entry, historyId);
    if (!(await entry.pending)) return false;
    return this.flush(item.id);
  }
  private async write(entry: Entry, historyId?: string): Promise<boolean> {
    const requested = entry.snapshot.text;
    const revision = entry.snapshot.caption.revision;
    // Keep recovery controls mounted during a blur-triggered retry so clicking
    // Review current sidecar can finish its pointer/focus event sequence.
    this.publish(entry, { status: 'Saving…', saving: true });
    try {
      const url = `/api/datasets/${this.datasetId}/items/${entry.item.id}/caption`;
      const caption = await (historyId
        ? saveCaption<Caption>(`${url}/restore`, 'POST', {
            history_id: historyId,
            revision,
          })
        : saveCaption<Caption>(url, 'PUT', { text: requested, revision }));
      const text =
        historyId && entry.snapshot.text === requested
          ? caption.text
          : entry.snapshot.text;
      this.publish(entry, {
        caption,
        text,
        error: '',
        status: text === caption.text ? 'Saved' : 'Unsaved changes',
      });
      this.onSaved(entry.item.id, caption);
      return true;
    } catch (reason) {
      this.publish(entry, {
        status: 'Save failed — text retained',
        error: (reason as Error).message,
      });
      return false;
    } finally {
      entry.pending = undefined;
      this.publish(entry, { saving: false });
    }
  }
  async flushAll(): Promise<boolean> {
    for (;;) {
      const entries = [...this.entries.values()].filter(
        (entry) =>
          entry.pending ||
          entry.snapshot.text !== entry.snapshot.caption.text ||
          entry.snapshot.error,
      );
      if (!entries.length) return true;
      const results = await Promise.all(
        entries.map((entry) => this.flush(entry.item.id)),
      );
      if (results.some((saved) => !saved)) return false;
    }
  }
  replace(item: DatasetItem, caption: Caption, keepDraft = false) {
    const entry = this.entry(item);
    if (entry.pending || entry.snapshot.saving) return false;
    window.clearTimeout(entry.timer);
    this.publish(entry, {
      caption,
      text: keepDraft ? entry.snapshot.text : caption.text,
      status: keepDraft ? 'Unsaved changes' : 'Saved',
      error: '',
    });
    this.onSaved(item.id, caption);
    return true;
  }
  firstFailed(): DatasetItem | undefined {
    return [...this.entries.values()].find((entry) => entry.snapshot.error)
      ?.item;
  }
  dispose() {
    this.entries.forEach((entry) => window.clearTimeout(entry.timer));
  }
}

export function useCaptionSession(
  datasetId: string,
  onSaved: (id: string, caption: Caption) => void,
) {
  const session = useMemo(
    () => new CaptionSession(datasetId, onSaved),
    [datasetId, onSaved],
  );
  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => {
      if (session.getSummary().unsaved) event.preventDefault();
    };
    window.addEventListener('beforeunload', protect);
    return () => {
      window.removeEventListener('beforeunload', protect);
      session.dispose();
    };
  }, [session]);
  return session;
}

export function useCaption(session: CaptionSession, item: DatasetItem) {
  return useSyncExternalStore(
    useCallback(
      (listener) => session.subscribe(item.id, listener),
      [session, item.id],
    ),
    useCallback(() => session.get(item), [session, item]),
  );
}
