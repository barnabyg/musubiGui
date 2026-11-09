export const screens = ['Datasets', 'Jobs', 'Runs', 'Settings'] as const;
export type Screen = (typeof screens)[number];
export type ItemView = 'caption' | 'audio' | 'preview';
export type DatasetRepair = { jobId: string; itemId?: string; view: ItemView };
export type Route = {
  screen: Screen;
  id?: string;
  repair?: DatasetRepair;
  review?: boolean;
};

const routePattern = /^#\/([a-z]+)(?:\/([\w-]+))?(?:\/items\/([\w-]+))?\/?$/;

// Hash routes need no server changes and no router dependency: `#/jobs/<id>`, `#/runs/<id>`.
export function parseRoute(hash: string): Route {
  const [path, query] = hash.split('?');
  const [, name, id, itemId] = routePattern.exec(path) ?? [];
  const params = new URLSearchParams(query);
  const screen = screens.find((value) => value.toLowerCase() === name);
  if (!screen) return { screen: 'Settings' };
  const jobId = params.get('job');
  if (id && screen === 'Datasets' && jobId && /^[\w-]+$/.test(jobId)) {
    const view = params.get('view');
    return {
      screen,
      id,
      repair: {
        jobId,
        itemId,
        view: view === 'caption' || view === 'audio' ? view : 'preview',
      },
    };
  }
  if (id && screen === 'Jobs' && params.get('review') === '1')
    return { screen, id, review: true };
  return id && screen !== 'Settings' ? { screen, id } : { screen };
}

// False for a bare address, which opens wherever is useful instead of a fixed screen.
export function namesScreen(hash: string) {
  const [, name] = routePattern.exec(hash.split('?')[0]) ?? [];
  return screens.some((value) => value.toLowerCase() === name);
}

export function formatRoute({ screen, id, repair, review }: Route) {
  const base = `#/${screen.toLowerCase()}${id ? `/${id}` : ''}`;
  if (repair) {
    const params = new URLSearchParams({
      view: repair.view,
      job: repair.jobId,
    });
    return `${base}${repair.itemId ? `/items/${repair.itemId}` : ''}?${params}`;
  }
  return review ? `${base}?review=1` : base;
}

export function datasetItemRoute(
  datasetId: string,
  itemId: string | undefined,
  view: ItemView,
  jobId: string,
) {
  return formatRoute({
    screen: 'Datasets',
    id: datasetId,
    repair: { jobId, itemId, view },
  });
}
