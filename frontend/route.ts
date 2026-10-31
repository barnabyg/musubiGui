export const screens = ['Datasets', 'Jobs', 'Runs', 'Settings'] as const;
export type Screen = (typeof screens)[number];
export type Route = { screen: Screen; id?: string };

// Hash routes need no server changes and no router dependency: `#/jobs/<id>`, `#/runs/<id>`.
export function parseRoute(hash: string): Route {
  const [, name, id] = /^#\/([a-z]+)(?:\/([\w-]+))?\/?$/.exec(hash) ?? [];
  const screen = screens.find((value) => value.toLowerCase() === name);
  if (!screen) return { screen: 'Settings' };
  return id && screen !== 'Settings' ? { screen, id } : { screen };
}

export function formatRoute({ screen, id }: Route) {
  return `#/${screen.toLowerCase()}${id ? `/${id}` : ''}`;
}
