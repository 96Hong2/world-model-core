// 라우트. **모든 화면이 URL 로 열려야 한다**(요구 §4 · 딥링크).
//
//   /ask?q=...                 질문과 그 답
//   /explore                   둘러보기 첫 화면
//   /explore/:type             종류별 목록 (account, need, capability …)
//   /explore/:type/:id         대상 상세
//   /library                   서재 목록
//   /library/:sourceId         자료 상세
//   /changes                   실행 목록
//   /changes/:runId            실행 상세
//   /saved                     저장함
//   /health                    데이터 상태

import { createBrowserRouter, Navigate } from "react-router-dom";
import { WmProvider } from "./WmProvider";
import { AppShell } from "../shell/AppShell";
import { AskPage } from "../pages/ask/AskPage";
import { ExplorePage } from "../pages/explore/ExplorePage";
import { EntityListPage } from "../pages/explore/EntityListPage";
import { EntityDetailPage } from "../pages/explore/EntityDetailPage";
import { LibraryPage } from "../pages/library/LibraryPage";
import { SourceDetailPage } from "../pages/library/SourceDetailPage";
import { ChangesPage } from "../pages/changes/ChangesPage";
import { RunDetailPage } from "../pages/changes/RunDetailPage";
import { SavedPage } from "../pages/saved/SavedPage";
import { DataHealthPage } from "../pages/health/DataHealthPage";
import { RouteError } from "./RouteError";

/** 라우터 바깥에 Provider 를 두면 라우트가 바뀔 때 상태가 날아간다. 안쪽에 둔다. */
function Root() {
  return (
    <WmProvider>
      <AppShell />
    </WmProvider>
  );
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Root />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Navigate to="/ask" replace /> },
      { path: "ask", element: <AskPage /> },
      { path: "explore", element: <ExplorePage /> },
      { path: "explore/:type", element: <EntityListPage /> },
      { path: "explore/:type/:id", element: <EntityDetailPage /> },
      { path: "library", element: <LibraryPage /> },
      { path: "library/:sourceId", element: <SourceDetailPage /> },
      { path: "changes", element: <ChangesPage /> },
      { path: "changes/:runId", element: <RunDetailPage /> },
      { path: "saved", element: <SavedPage /> },
      { path: "health", element: <DataHealthPage /> },
      { path: "*", element: <RouteError /> },
    ],
  },
]);
