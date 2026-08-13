"""Neo4j 드라이버 팩토리.

읽기 전용 경로와 쓰기 경로를 서로 다른 타입으로 분리한다.
질의 계층(Answer API·Retrieval)은 `ReadOnlyGraph` 만 받고, 이 객체에는 쓰기 API 자체가 없다.
세션은 서버가 강제하는 read access mode 로 열리므로 CREATE/MERGE 는 서버에서 거부된다.

접속 설정은 환경변수로 주입한다.
    BWM_NEO4J_URI / BWM_NEO4J_USER / BWM_NEO4J_PASSWORD / BWM_NEO4J_DATABASE
읽기 경로에 별도 계정을 쓰려면 아래를 추가로 준다(없으면 위 값으로 폴백).
    BWM_NEO4J_READ_USER / BWM_NEO4J_READ_PASSWORD
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from neo4j import READ_ACCESS, WRITE_ACCESS, Driver, GraphDatabase, Session

DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "bwmdemo123"  # 로컬 데모 컨테이너 기본값(infra/docker-compose.yml 과 동일)
DEFAULT_DATABASE = "neo4j"


@dataclass(frozen=True)
class Neo4jSettings:
    uri: str = DEFAULT_URI
    user: str = DEFAULT_USER
    password: str = DEFAULT_PASSWORD
    database: str = DEFAULT_DATABASE

    @classmethod
    def from_env(cls, *, read_only: bool = False) -> "Neo4jSettings":
        user = os.getenv("BWM_NEO4J_USER", DEFAULT_USER)
        password = os.getenv("BWM_NEO4J_PASSWORD", DEFAULT_PASSWORD)
        if read_only:
            user = os.getenv("BWM_NEO4J_READ_USER") or user
            password = os.getenv("BWM_NEO4J_READ_PASSWORD") or password
        return cls(
            uri=os.getenv("BWM_NEO4J_URI", DEFAULT_URI),
            user=user,
            password=password,
            database=os.getenv("BWM_NEO4J_DATABASE", DEFAULT_DATABASE),
        )

    def __repr__(self) -> str:  # 비밀번호가 로그·트레이스백에 찍히지 않게 한다.
        return f"Neo4jSettings(uri={self.uri!r}, user={self.user!r}, database={self.database!r})"


def _build_driver(settings: Neo4jSettings) -> Driver:
    return GraphDatabase.driver(settings.uri, auth=(settings.user, settings.password))


class ReadOnlyGraph:
    """질의 전용 핸들. 쓰기 메서드가 없다."""

    def __init__(self, settings: Neo4jSettings | None = None, driver: Driver | None = None):
        self.settings = settings or Neo4jSettings.from_env(read_only=True)
        self._driver = driver or _build_driver(self.settings)
        self._owns_driver = driver is None

    def session(self, **kwargs: Any) -> Session:
        return self._driver.session(
            database=self.settings.database, default_access_mode=READ_ACCESS, **kwargs
        )

    def execute_read(self, work: Callable[[Session], Any]) -> Any:
        with self.session() as session:
            return session.execute_read(lambda tx: work(tx))

    def verify_connectivity(self) -> None:
        self._driver.verify_connectivity()

    def close(self) -> None:
        if self._owns_driver:
            self._driver.close()

    def __enter__(self) -> "ReadOnlyGraph":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class WritableGraph:
    """적재·DDL 경로 핸들. 읽기 세션도 열 수 있지만 그 세션은 read access mode 다."""

    def __init__(self, settings: Neo4jSettings | None = None, driver: Driver | None = None):
        self.settings = settings or Neo4jSettings.from_env()
        self._driver = driver or _build_driver(self.settings)
        self._owns_driver = driver is None

    def write_session(self, **kwargs: Any) -> Session:
        return self._driver.session(
            database=self.settings.database, default_access_mode=WRITE_ACCESS, **kwargs
        )

    def read_session(self, **kwargs: Any) -> Session:
        return self._driver.session(
            database=self.settings.database, default_access_mode=READ_ACCESS, **kwargs
        )

    def execute_write(self, work: Callable[[Session], Any]) -> Any:
        with self.write_session() as session:
            return session.execute_write(lambda tx: work(tx))

    def verify_connectivity(self) -> None:
        self._driver.verify_connectivity()

    def close(self) -> None:
        if self._owns_driver:
            self._driver.close()

    def __enter__(self) -> "WritableGraph":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def read_only_graph(settings: Neo4jSettings | None = None) -> ReadOnlyGraph:
    return ReadOnlyGraph(settings)


def writable_graph(settings: Neo4jSettings | None = None) -> WritableGraph:
    return WritableGraph(settings)


def require_read_only(graph: Any) -> ReadOnlyGraph:
    """질의 계층 진입점에서 쓰기 핸들이 흘러 들어오는 것을 막는다."""
    if not isinstance(graph, ReadOnlyGraph):
        raise TypeError(f"질의 경로에는 ReadOnlyGraph 만 받는다. 받은 타입: {type(graph).__name__}")
    return graph
