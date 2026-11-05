import type { NextConfig } from "next";

// 로컬 개발: Next(3000) → FastAPI(uvicorn 8000)로 /api/* 프록시
// 배포(Vercel): vercel.json rewrites가 /api/* → api/index.py 로 연결
const nextConfig: NextConfig = {
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
