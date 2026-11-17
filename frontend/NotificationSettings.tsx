import { useState } from 'react';
import { notificationsWanted, setNotificationsWanted } from './runSignals';

export function NotificationSettings() {
  const [wanted, setWanted] = useState(notificationsWanted);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const available = 'Notification' in window;
  const denied = available && Notification.permission === 'denied';
  async function change(value: boolean) {
    setPending(true);
    try {
      const enabled = await setNotificationsWanted(value);
      setWanted(enabled);
      setMessage(
        value && !enabled
          ? 'Browser permission is required to send notifications.'
          : '',
      );
    } catch {
      setMessage('The browser could not change notification permission.');
    } finally {
      setPending(false);
    }
  }
  const hint = !available
    ? 'This browser does not support notifications.'
    : denied
      ? 'Notifications are blocked in this browser’s site settings.'
      : message;
  return (
    <section className="panel" aria-label="Notifications">
      <h2>Notifications</h2>
      <label className="job-toggle">
        <input
          type="checkbox"
          checked={wanted && !denied}
          disabled={pending || !available || denied}
          onChange={(event) => void change(event.target.checked)}
        />
        <span>Send a browser notification when a Run is ready or finished</span>
      </label>
      {hint && <p aria-live="polite">{hint}</p>}
    </section>
  );
}
