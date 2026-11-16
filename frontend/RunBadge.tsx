import { runStatus } from './runStatus';
import { StatusBadge } from './StatusBadge';

export function RunBadge({ status }: { status: string }) {
  const { label, tone } = runStatus(status);
  return <StatusBadge tone={tone}>{label}</StatusBadge>;
}
