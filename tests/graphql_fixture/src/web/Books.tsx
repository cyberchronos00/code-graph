import { gql, useMutation, useQuery, useSubscription } from '@apollo/client';
import { ADD_BOOK, GET_BOOKS, IS_LOGGED_IN, MISSING } from './queries';

export function Books() {
  const { data } = useQuery<{ books: { id: string }[] }>(GET_BOOKS, { variables: { first: 5 } });
  return data;
}

export function AddBook() {
  const [add] = useMutation(ADD_BOOK, { refetchQueries: [{ query: GET_BOOKS }] });
  return add;
}

export function LoginState() {
  const { data } = useQuery(IS_LOGGED_IN);
  return data;
}

export async function loadMissing(client) {
  return client.query({ query: MISSING });
}

export function readCached(client) {
  return client.readQuery({ query: GET_BOOKS });
}

export function LiveBooks() {
  const { data } = useSubscription(gql`
    subscription OnBookAdded {
      bookAdded { id title }
    }
  `);
  return data;
}
