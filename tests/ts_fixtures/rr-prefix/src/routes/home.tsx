import { Link } from "react-router";

export async function loader() {
  return null;
}

export default function Home() {
  return <Link to="shop/books/1">book</Link>;
}
