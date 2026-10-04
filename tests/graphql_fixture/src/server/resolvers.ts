import { ApolloServer } from '@apollo/server';
import { listBooks, findAuthor, saveBook, pubsub } from './store';

const bookQueries = {
  books: () => listBooks(),
};

function statsResolver() {
  return { books: listBooks().length };
}

export const resolvers = {
  Query: {
    ...bookQueries,
    author(_parent, { id }) {
      return findAuthor(id);
    },
    stats: statsResolver,
  },
  Mutation: {
    addBook: async (_parent, { title }) => saveBook(title),
  },
  Subscription: {
    bookAdded: {
      subscribe: () => pubsub.asyncIterator(['BOOK_ADDED']),
    },
  },
  Book: {
    author: (book) => findAuthor(book.authorId),
  },
};

export const server = new ApolloServer({ typeDefs: [], resolvers });
