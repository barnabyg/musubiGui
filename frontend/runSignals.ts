import { useEffect, useRef } from 'react';
import { useRunHistory } from './runHistory';

const NOTIFY_KEY = 'musubi-run-notifications';
const signals: Record<string, string> = {
  prepared: 'Ready',
  completed: 'Finished',
  stopped: 'Stopped',
  failed: 'Failed',
};

export function notificationsWanted() {
  try {
    return (
      window.localStorage.getItem(NOTIFY_KEY) === 'yes' &&
      'Notification' in window &&
      Notification.permission === 'granted'
    );
  } catch {
    return false;
  }
}

// Browsers only grant permission from a user gesture, so this is called from the opt-in control.
export async function setNotificationsWanted(wanted: boolean) {
  const granted =
    wanted &&
    'Notification' in window &&
    (await Notification.requestPermission()) === 'granted';
  try {
    window.localStorage.setItem(NOTIFY_KEY, granted ? 'yes' : 'no');
  } catch {
    // Without storage the preference only lasts as long as the permission check above.
  }
  return granted;
}

// Tells the user, on any screen, when a Run stops needing them to wait. The signal stays in the
// tab title until they come back to the tab or change screen.
export function useRunSignals(screen: string) {
  const { runs } = useRunHistory();
  const known = useRef<Map<string, string> | undefined>(undefined);
  useEffect(() => {
    const original = document.title;
    const clear = () => {
      document.title = original;
    };
    window.addEventListener('focus', clear);
    return () => {
      window.removeEventListener('focus', clear);
      clear();
    };
  }, [screen]);
  useEffect(() => {
    if (!runs) return;
    if (known.current) {
      for (const run of runs) {
        const label = signals[run.status];
        if (!label || known.current.get(run.id) === run.status) continue;
        const message = `${label}: ${run.job.name}`;
        document.title = `${message} — Musubi`;
        if (notificationsWanted()) new Notification(message);
      }
    }
    known.current = new Map(runs.map((run) => [run.id, run.status]));
  }, [runs]);
}
