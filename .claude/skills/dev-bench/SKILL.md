---
name: dev-bench
description: Set up or reuse the throwaway Docker dev bench and run the test suite. Use for any "run the tests" or "try it on a bench" task.
---

# Dev bench (Docker, throwaway)

Containers: `fa-db` (mariadb:11.8) + `fa-bench` (frappe/bench, repo mounted read-only
at /mnt/frappe_agents) on network `fa-net`. Site `test_site`, passwords are dev-only.

## First-time setup

```bash
docker network create fa-net
docker run -d --name fa-db --network fa-net -e MYSQL_ROOT_PASSWORD=fa_root_pw \
  mariadb:11.8 --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci
docker run -d --name fa-bench --network fa-net \
  -v <repo>:/mnt/frappe_agents:ro frappe/bench:latest bash -c 'sleep infinity'

# bench init dies with exit 127 without this — the image has no redis-server:
docker exec -u root fa-bench bash -c 'apt-get update -qq && apt-get install -y -qq redis-server mariadb-client'

docker exec fa-bench bash -lc 'cd /home/frappe && bench init --skip-assets --frappe-branch version-16 frappe-bench'
docker exec fa-bench bash -lc 'redis-server --daemonize yes --port 13000 && redis-server --daemonize yes --port 11000'
docker exec fa-bench bash -lc 'cd /home/frappe/frappe-bench && bench set-config -g db_host fa-db && bench set-config -g redis_cache redis://localhost:13000 && bench set-config -g redis_queue redis://localhost:11000 && bench set-config -g redis_socketio redis://localhost:11000'
docker exec fa-bench bash -lc 'cd /home/frappe/frappe-bench && bench new-site test_site --db-root-password fa_root_pw --admin-password admin --mariadb-user-host-login-scope=%'
docker exec fa-bench bash -lc 'cd /home/frappe/frappe-bench && bench get-app frappe_agents /mnt/frappe_agents && bench --site test_site install-app frappe_agents && bench --site test_site set-config allow_tests true'
```

## Every run after a change on main

```bash
docker exec fa-bench bash -lc 'cd /home/frappe/frappe-bench/apps/frappe_agents && git pull /mnt/frappe_agents main'
docker exec fa-bench bash -lc 'cd /home/frappe/frappe-bench && bench --site test_site migrate && bench --site test_site run-tests --app frappe_agents'
```

Rules: run tests against committed main, not the working tree — get-app clones from
the mount. If redis dies (container restart), rerun the two redis-server lines.
Tear down with `docker rm -f fa-db fa-bench` when done; nothing in it is precious.

Last verified: 2026-08-13 — 99 tests green on Frappe v16 / Python 3.14.2.
