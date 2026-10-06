import { NavLink, useNavigate as useNav } from "react-router-dom";

export function bookLoader() {
  return null;
}

export function bookAction() {
  return null;
}

export default function Book({ id }: { id: string }) {
  const navigate = useNav();
  const back = -1;
  return (
    <div>
      <button type="button" onClick={() => navigate("reviews")}>reviews</button>
      <button type="button" onClick={() => navigate("../about")}>about</button>
      <button type="button" onClick={() => navigate("missing")}>missing</button>
      <button type="button" onClick={() => navigate(-1)}>back</button>
      <button type="button" onClick={() => navigate(back)}>back again</button>
      <button type="button" onClick={() => navigate("/hashed")}>hashed</button>
      <NavLink to={`/books/${id}/reviews`}>reviews link</NavLink>
      <NavLink to="/files/a/b">files</NavLink>
      <NavLink to="/users/5">user</NavLink>
    </div>
  );
}

export function Reviews() {
  return <p>Reviews</p>;
}
