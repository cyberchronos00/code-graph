import { gql } from '@apollo/client';

export const BOOK_PARTS = gql`
  fragment BookParts on Book {
    id
    title
    author { name }
  }
`;

export const GET_BOOKS = gql`
  query GetBooks($first: Int) {
    books(first: $first) {
      ...BookParts
      __typename
    }
  }
  ${BOOK_PARTS}
`;

export const ADD_BOOK = gql`
  mutation AddBook($title: String!) {
    created: addBook(title: $title) {
      id
    }
  }
`;

export const IS_LOGGED_IN = gql`
  query IsLoggedIn {
    isLoggedIn @client
  }
`;

export const MISSING = gql`
  query Missing {
    retired { id }
  }
`;

// export const OLD = gql`query Old { ping }`;
