import { index, layout, prefix, route } from "@react-router/dev/routes";

export default [
  layout("routes/shop.tsx", [
    index("routes/home.tsx"),
    prefix("shop", [
      route("books/:id", "routes/book.tsx"),
    ]),
    route("dash", "routes/dash.tsx", [
      route("/login", "routes/login.tsx"),
    ]),
  ]),
];
