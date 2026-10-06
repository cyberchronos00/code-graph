"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

export default function Home() {
  const router = useRouter();
  return (
    <div>
      <Link href="/books/1">One</Link>
      <button type="button" onClick={() => router.push("/books/2")}>Two</button>
    </div>
  );
}
