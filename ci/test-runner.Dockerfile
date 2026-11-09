# (과제 CI) pytest 실행 전용 이미지 — Jenkins Test 단계가 빌드 전용 PostgreSQL 컨테이너와 네트워크 이름공간을 공유해 실행한다.
# - Python 3.13: 앱 이미지(fastapi-app/Dockerfile)와 CLAUDE.md 명세와 같은 버전
# - bookworm(Debian 12): Playwright --with-deps 가 공식 지원하는 배포판
# - 빌드 컨텍스트에는 requirements 두 파일과 이 Dockerfile 만 넣는다 (Jenkins 가 임시 폴더에 복사). 소스·비밀값은 넣지 않는다.
# - 레이어 캐시 덕분에 requirements 가 바뀌지 않으면 다시 설치하지 않는다.
FROM python:3.13-slim-bookworm

ARG PLAYWRIGHT_VERSION=1.65.0
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY requirements.txt requirements-dev.txt /tmp/req/
RUN pip install -r /tmp/req/requirements-dev.txt "playwright==${PLAYWRIGHT_VERSION}" \
 && python -m playwright install --with-deps chromium \
 && chmod -R a+rX /ms-playwright \
 && rm -rf /tmp/req /var/lib/apt/lists/*

# 실행은 Jenkins 사용자 uid 로 한다(-u). 작업 폴더는 마운트한 워크스페이스.
WORKDIR /work
