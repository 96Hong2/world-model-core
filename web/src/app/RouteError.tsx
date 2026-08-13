// 라우트가 없거나 화면이 터졌을 때. 기술 오류 대신 **다음에 무엇을 할지** 적는다.

import { Link, useRouteError } from "react-router-dom";

export function RouteError() {
  const error = useRouteError() as { status?: number; message?: string } | null;
  const notFound = !error || error.status === 404;

  return (
    <div className="screen">
      <div className="notice" data-tone="error">
        <div className="notice-title">
          {notFound
            ? "이 주소에는 화면이 없습니다"
            : "화면을 그리지 못했습니다"}
        </div>
        <div className="notice-body">
          {notFound
            ? "주소가 바뀌었거나 잘못 입력됐습니다. 아래에서 가고 싶은 곳을 골라 주세요."
            : "화면 코드에서 문제가 났습니다. 다시 시도해도 같으면 개발자에게 알려 주세요."}
        </div>
        {!notFound && error?.message && (
          <div className="notice-hint mono">{error.message}</div>
        )}
        <div className="row">
          <Link to="/ask" className="btn" data-variant="primary">
            묻기로 가기
          </Link>
          <Link to="/explore" className="btn" data-variant="outline">
            둘러보기
          </Link>
          <Link to="/library" className="btn" data-variant="outline">
            서재
          </Link>
        </div>
      </div>
    </div>
  );
}
