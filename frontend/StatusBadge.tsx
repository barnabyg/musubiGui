import type { ReactNode } from 'react';
import {
  Circle,
  CircleCheck,
  CircleX,
  LoaderCircle,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';

export type Tone = 'success' | 'running' | 'warning' | 'failure' | 'neutral';

const toneIcons: Record<Tone, LucideIcon> = {
  success: CircleCheck,
  running: LoaderCircle,
  warning: TriangleAlert,
  failure: CircleX,
  neutral: Circle,
};

// Status in words and shape as well as colour.
export function StatusBadge({
  tone,
  children,
}: {
  tone: Tone;
  children: ReactNode;
}) {
  const Icon = toneIcons[tone];
  return (
    <span className={`status-badge ${tone}`}>
      <Icon />
      {children}
    </span>
  );
}
