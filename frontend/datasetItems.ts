import type { DatasetItem } from './Datasets';

export function hasCaption(item: DatasetItem) {
  return !!item.caption.text.trim();
}

export function matchingItems(
  items: DatasetItem[],
  query: string,
  filter: string,
  sort: string,
  selected: Set<string>,
) {
  return items
    .filter((item) => {
      const matches = (item.name + '\n' + item.caption.text)
        .toLowerCase()
        .includes(query.toLowerCase());
      return (
        matches &&
        (filter === 'all' ||
          (filter === 'missing' && !hasCaption(item)) ||
          (filter === 'invalid' && !item.valid) ||
          (filter === 'excluded' && item.excluded) ||
          (filter === 'included' && item.valid && !item.excluded) ||
          (filter === 'selected' && selected.has(item.id)))
      );
    })
    .sort((a, b) =>
      sort === 'name-desc'
        ? b.name.localeCompare(a.name)
        : sort === 'caption'
          ? a.caption.text.localeCompare(b.caption.text) ||
            a.name.localeCompare(b.name)
          : a.name.localeCompare(b.name),
    );
}
