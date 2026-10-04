export const pubsub = { asyncIterator: (names: string[]) => names };
export function listBooks() { return []; }
export function findAuthor(id: string) { return { id }; }
export function saveBook(title: string) { return { id: '1', title }; }
