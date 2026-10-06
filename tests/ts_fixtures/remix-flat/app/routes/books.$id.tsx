import { Form, useFetcher, useSubmit } from "@remix-run/react";

export async function loader({ params }: { params: { id: string } }) {
  return params.id;
}

export async function action() {
  return null;
}

export async function clientLoader() {
  return null;
}

export async function clientAction() {
  return null;
}

export default function Book() {
  const fetcher = useFetcher();
  const submit = useSubmit();
  return (
    <div>
      <Form method="post" />
      <Form method="delete" />
      <Form method="post" action="edit" />
      <Form method="put" action="/books/1" />
      <fetcher.Form method="post" action="/books/1" />
      <button type="button" onClick={() => { fetcher.load("/books/1"); fetcher.submit(null, { method: "post", action: "/books/1" }); submit(null, { method: "delete", action: "/books/1" }); }}>go</button>
    </div>
  );
}
