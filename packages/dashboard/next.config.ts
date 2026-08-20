import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the workspace root. Without this, Turbopack walks up looking for a
  // lockfile and finds a stray package-lock.json in the home directory, which
  // sits outside the repo — so it warns and infers the wrong root.
  turbopack: {
    root: path.resolve(__dirname),
  },
};

export default nextConfig;
