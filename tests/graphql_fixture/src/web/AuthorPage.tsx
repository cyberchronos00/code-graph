import { useAuthorQuery } from './__generated__/hooks';

export function AuthorPage({ id }) {
  const { data } = useAuthorQuery({ variables: { id } });
  return data;
}
