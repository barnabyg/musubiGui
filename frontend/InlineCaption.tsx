import type { DatasetItem } from './Datasets';
import { type CaptionSession, useCaption } from './captionSession';

export function InlineCaption({
  session,
  item,
  onFocus,
  onReview,
}: {
  session: CaptionSession;
  item: DatasetItem;
  onFocus: () => void;
  onReview: () => void;
}) {
  const state = useCaption(session, item);
  return (
    <div className={`inline-caption ${state.error ? 'has-error' : ''}`}>
      <textarea
        aria-label={`Caption for ${item.name}`}
        placeholder="Add a caption…"
        value={state.text}
        disabled={Boolean(state.caption.error)}
        onFocus={onFocus}
        onChange={(event) => session.change(item, event.target.value)}
        onBlur={() => {
          void session.flush(item.id);
        }}
      />
      <div className="inline-caption-status">
        <small title={state.error || state.status}>
          {state.error
            ? 'Save failed — draft retained'
            : state.status === 'Caption loaded'
              ? ''
              : state.status}
        </small>
        {state.error && <button onClick={onReview}>Review caption</button>}
      </div>
    </div>
  );
}
