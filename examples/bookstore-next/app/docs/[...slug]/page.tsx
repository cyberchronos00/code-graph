export default async function DocsPage({ params }: { params: Promise<{ slug: string[] }> }) {
  return <article>{(await params).slug.join('/')}</article>;
}
