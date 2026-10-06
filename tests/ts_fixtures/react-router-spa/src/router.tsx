import { createBrowserRouter, createHashRouter, createMemoryRouter, createRoutesFromElements, Link, Route, Routes, useRoutes } from "react-router-dom";
import Book, { bookAction, bookLoader, Reviews } from "./book";

export function Shell() {
  return <div />;
}

export function Home() {
  return <Link to="/books/1">book</Link>;
}

export function About() {
  return <p>About</p>;
}

export function Admin() {
  return <div />;
}

export function AdminHome() {
  return <p>Admin</p>;
}

export function Users() {
  return <p>Users</p>;
}

export function Legacy() {
  return <div />;
}

export function LegacyHome() {
  return <p>Legacy</p>;
}

export const hashed = createHashRouter([{ path: "hashed", Component: About }]);

export const memory = createMemoryRouter([{ path: "memory", Component: About }]);

export function InMemory() {
  return useRoutes([{ path: "hooked", Component: About }]);
}

export const router = createBrowserRouter([
  {
    path: "/",
    Component: Shell,
    children: [
      { index: true, Component: Home },
      {
        path: "books/:id",
        lazy: () => import("./book"),
        loader: bookLoader,
        action: bookAction,
        children: [{ path: "reviews", Component: Reviews }],
      },
      { path: "about", element: <About /> },
      { path: "users/:id?", Component: About },
      { path: "files/*", Component: About },
      {
        path: "dash",
        Component: Shell,
        children: [{ path: "/absolute", Component: About }],
      },
    ],
  },
]);

export const legacy = createRoutesFromElements(
  <Route path="legacy" element={<Legacy />}>
    <Route index element={<LegacyHome />} />
  </Route>
);

export function JsxRoutes() {
  return (
    <Routes>
      <Route path="admin" element={<Admin />}>
        <Route index element={<AdminHome />} />
        <Route path="users" element={<Users />} />
      </Route>
    </Routes>
  );
}

void Book;
