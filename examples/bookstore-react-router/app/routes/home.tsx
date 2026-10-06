import { Link } from "react-router";
import { BookCard } from "../components/BookCard";

export async function loader() {
  const res = await fetch("/api/books/");
  return res.json();
}

export default function Home() {
  const books = [{ id: 1, title: "Dune" }];
  return (
    <ul>
      {books.map((b) => (
        <li key={b.id}>
          <Link to={`/books/${b.id}`}>{b.title}</Link>
          <BookCard book={b} />
        </li>
      ))}
    </ul>
  );
}
