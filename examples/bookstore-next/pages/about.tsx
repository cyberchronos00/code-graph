import { GetStaticProps } from 'next';

export const getStaticProps: GetStaticProps = async () => ({ props: { version: process.env.NEXT_PUBLIC_APP_VERSION ?? 'dev' } });

export default function About({ version }: { version: string }) {
  return <p>Bookstore {version}</p>;
}
