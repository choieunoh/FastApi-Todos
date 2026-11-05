-- (7.0.0) 개발용 PostgreSQL 컨테이너를 처음 만들 때 한 번 실행된다 (docker-compose.dev.yml).
-- 테스트 전용 DB. 테스트는 이름이 _test 로 끝나는 DB 에만 연결한다 (tests/conftest.py).
CREATE DATABASE todo_test;
