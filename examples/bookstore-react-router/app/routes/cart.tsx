import { redirect } from "react-router";

export async function loader() {
  const cart: { id: number }[] = [];
  if (cart.length === 0) return redirect("/");
  return cart;
}

export default function Cart() {
  return <p>Cart</p>;
}
