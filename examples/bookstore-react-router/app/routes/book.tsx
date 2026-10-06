import { Form, useNavigate } from "react-router";

export async function loader({ params }: { params: { id: string } }) {
  const res = await fetch(`/api/books/${params.id}/`);
  return res.json();
}

export async function action() {
  await fetch("/api/orders/", { method: "POST" });
  return null;
}

export default function Book() {
  const navigate = useNavigate();
  return (
    <Form
      method="post"
      onSubmit={() => {
        navigate("/cart");
      }}
    >
      <button type="submit">Place order</button>
    </Form>
  );
}
