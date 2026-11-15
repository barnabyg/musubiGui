export type PreviewMemoryChoice = {
  mode: 'automatic' | 'fallback' | 'training-residency';
  blocks_to_swap: number;
  reserve_bytes: number;
  reason: string;
};

export function PreviewMemory({
  stages,
}: {
  stages: { preview_memory?: PreviewMemoryChoice }[];
}) {
  const choices = stages.flatMap((stage) =>
    stage.preview_memory ? [stage.preview_memory] : [],
  );
  const unique = choices.filter(
    (choice, index) =>
      choices.findIndex(
        (other) =>
          other.mode === choice.mode &&
          other.blocks_to_swap === choice.blocks_to_swap,
      ) === index,
  );
  if (!unique.length) return null;
  return (
    <div aria-label="Preview memory settings">
      {unique.map((choice) => (
        <p key={choice.mode}>
          {choice.mode === 'training-residency'
            ? 'In-run preview memory: training residency retained. '
            : choice.mode === 'automatic'
              ? 'Generation memory: automatic with reserved GPU headroom. '
              : 'Generation memory: conservative family preset. '}
          {choice.reason}
        </p>
      ))}
    </div>
  );
}
