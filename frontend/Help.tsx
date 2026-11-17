import {
  createContext,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { CircleHelp } from 'lucide-react';

const topics = {
  'Background training':
    'You can close the browser after submitting training. Keep the local backend and computer running. Reopen Runs to check progress or stop a Run.',
  'Frozen revisions':
    'Preparation saves a copy of the reviewed job settings, included media and captions for that Run. Editing the job or dataset affects future preparations. Open the originating job to edit its current draft.',
  'Cache reuse':
    'Preparation checks the latent and text embedding caches against the selected inputs and settings. Compatible artifacts can be copied into the new Run. Missing or incompatible items are rebuilt. Choosing Rebuild cache also rebuilds compatible items. Earlier Runs keep their own files.',
  'Time estimates':
    'Training ETA is the trainer’s forecast for its current training stage. In alternating Wan training, Phase ETA covers only the current expert phase. Estimated training remaining projects the observed step speed to the target, excluding future model loading, previews and final sampling. Estimated Run remaining also budgets measured sampling and overhead. A partial estimate covers only the measured components. Unknown means the required timings are still unavailable.',
  'Model paths':
    'Settings holds the model, dataset, output and cache folders for this installation. Jobs inherit the model component files configured there. A path entered under Job & dataset overrides that component for the job and must be inside a configured model folder.',
};
type Topic = keyof typeof topics;
const OpenHelp = createContext<(topic: Topic) => void>(() => {});

export function HelpLink({
  topic,
  entry = false,
}: {
  topic: Topic;
  entry?: boolean;
}) {
  const open = useContext(OpenHelp);
  return (
    <button
      type="button"
      className={entry ? 'quiet help-entry' : 'help-link'}
      onClick={() => open(topic)}
    >
      {entry ? (
        <>
          <CircleHelp /> Help
        </>
      ) : (
        topic
      )}
    </button>
  );
}

export function WorkspaceHelp({ children }: { children: ReactNode }) {
  const [topic, setTopic] = useState<Topic>('Background training');
  const dialog = useRef<HTMLDialogElement>(null);
  function open(selected: Topic) {
    setTopic(selected);
    dialog.current?.showModal();
  }
  return (
    <OpenHelp value={open}>
      {children}
      <dialog
        ref={dialog}
        className="help-dialog"
        aria-labelledby="workspace-help-title"
      >
        <h2 id="workspace-help-title">Help</h2>
        <label className="field">
          Help topic
          <select
            value={topic}
            onChange={(event) => setTopic(event.target.value as Topic)}
          >
            {Object.keys(topics).map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <h3>{topic}</h3>
        <p>{topics[topic]}</p>
        <button onClick={() => dialog.current?.close()}>Close help</button>
      </dialog>
    </OpenHelp>
  );
}
