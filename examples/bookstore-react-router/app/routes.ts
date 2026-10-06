import { index, layout, route, type RouteConfig } from "@react-router/dev/routes";

export default [
  layout("routes/shop-layout.tsx", [
    index("routes/home.tsx"),
    route("books/:id", "routes/book.tsx"),
    route("cart", "routes/cart.tsx"),
  ]),
] satisfies RouteConfig;
