import type { NextConfig } from "next";

const API = process.env.TWN_API_URL ?? "http://127.0.0.1:8180";

const nextConfig: NextConfig = {
  // 화면과 API 를 같은 주소로 — 세션 쿠키가 그대로 붙는다.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};

export default nextConfig;
