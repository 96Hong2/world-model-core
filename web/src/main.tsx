import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";

// 글꼴은 CDN 이 아니라 npm 패키지에서 자체 호스팅한다. 사내망·오프라인 데모에서
// 글꼴이 조용히 시스템 기본으로 바뀌면 화면 밀도가 통째로 달라진다.
// 나눔명조(답변 본문용)는 뺐다. 화면에서 한글 획이 얇아 깨진 것처럼 보였다.
// 이유는 web/DESIGN.md 「글자」 절에 적어 뒀다.
import "pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css";
import "@fontsource/jetbrains-mono/400.css";

import "./styles.css";
import { router } from "./app/routes";

const root = document.getElementById("root");
if (!root) throw new Error("#root 를 찾지 못했습니다.");

createRoot(root).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
