import { gql } from '@apollo/client';

export const SHELVES = gql`query Shelves { shelves }`;

export async function fetchShelves(client) {
  const res = await client.query({ query: SHELVES });
  return res.data;
}
