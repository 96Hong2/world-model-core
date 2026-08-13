import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 화면은 같은 출처의 `/api` 로만 부른다. dev 서버가 그것을 Answer API 로 넘겨 주므로
// 브라우저 쪽에서 CORS 를 다룰 일이 없다. 서버 주소가 다르면 VITE_API_TARGET 으로 바꾼다.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_API_TARGET || "http://localhost:8099";

  return {
    plugins: [react()],
    server: {
      port: 5273,
      strictPort: false,
      proxy: {
        "/api": {
          target,
          changeOrigin: true,
          // Q-S 는 LLM 합성이라 수십 초 걸린다. 기본 타임아웃에 잘리지 않게 넉넉히 둔다.
          timeout: 600_000,
          proxyTimeout: 600_000,
          rewrite: (path) => path.replace(/^\/api/, ""),
        },
      },
    },
    build: { outDir: "dist" },
  };
});
