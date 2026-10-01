/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: '/catalog/:id', destination: '/api/books/:id' }];
  },
};

module.exports = nextConfig;
