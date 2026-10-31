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

// False for a bare address, which opens wherever is useful instead of a fixed screen.
export function namesScreen(hash: string) {
  const [, name] = /^#\/([a-z]+)/.exec(hash) ?? [];
  return screens.some((value) => value.toLowerCase() === name);
}

export function formatRoute({ screen, id }: Route) {
  return `#/${screen.toLowerCase()}${id ? `/${id}` : ''}`;
}
