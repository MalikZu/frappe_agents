---
name: dev-bench
description: Run tests or hands-on-test frappe_agents on the local frappe_docker stack. Use for any "run the tests", "try it on a bench", or "rebuild the bench" task.
---

# Dev bench (frappe_docker compose stack "fa")

The old hand-rolled bench (fa-bench + hand-run redis/serve/socketio) is RETIRED —
its uncapped `bench build` froze the Mac on 2026-08-16. This stack is the official
frappe_docker layered image with hard resource caps.

Everything lives in `/Users/malik/Projects/fa-docker-bench/`:
`apps.json` (canonical; `frappe_docker/apps.json` is a symlink), `compose.yaml`
(project name `fa`, every service has mem_limit/cpus — totals ≤6.5G/7cpu),
`scripts/{setup_wizard,grant_roles,seed_agents}.py`, `build.log`, `tests*.log`.

Two sites on the stack:
- **test_site** — Malik's hands-on site: UAE company (Falcon Trading LLC, AED),
  ERPNext demo data, users Administrator/admin + malik@leam.ae/admin.
  **Never run the suite here** — test records (13 _Test Company rows) already
  polluted it once.
- **clean_test** — pristine, allow_tests, no wizard/demo. **All suite runs here.**

Apps baked in the image `fa-apps:v0.6.0-preview`: frappe v16, payments, erpnext,
hrms v16, frappe_agents (integration/v0.6.0-preview), flow_client. Gotcha: the
flow_client REPO installs as app **`flow`** (`--install-app flow`). LEGAL: never
read flow_client source (AGPL vs our MIT) — install/run as black box only.

## Daily driving

```bash
cd /Users/malik/Projects/fa-docker-bench
docker compose -p fa up -d          # start   (http://localhost:8010)
docker compose -p fa stop           # stop, keeps everything
docker compose -p fa down           # removes containers, KEEPS volumes — but
                                    # also LOSES any docker-cp'd app files (see below)
docker compose -p fa logs -f backend
docker exec fa-backend-1 bash -lc 'cd /home/frappe/frappe-bench && bench --site test_site console'
```

Login link without typing a password:
`docker exec fa-backend-1 bash -lc 'cd /home/frappe/frappe-bench && bench --site test_site browse --user Administrator'`
— take the sid, use `http://localhost:8010/app?sid=...` (ignore the broken host in
its output).

## Running the suite

```bash
docker exec fa-backend-1 bash -lc 'cd /home/frappe/frappe-bench && bench --site clean_test migrate && bench --site clean_test run-tests --app frappe_agents'
```
759 green on 2026-08-16 (integration branch). Check the COUNT moved as expected.

## Updating frappe_agents code on the running stack

The layered image STRIPS `.git` from apps — you cannot fetch inside the container.
Two ways:

1. **Quick (container-local, lost on `compose down`):** from a local worktree,
   `git diff --name-only <baked>..<new>` then `docker cp` each file into
   `fa-backend-1:/home/frappe/frappe-bench/apps/frappe_agents/<path>` (+ chown
   frappe:frappe as root), then migrate + clear-cache both sites and
   `docker compose -p fa restart backend queue-short queue-long scheduler`.
2. **Proper (bake it):** update the branch in `apps.json` if needed, then from
   `fa-docker-bench/frappe_docker/`, NOTHING ELSE HEAVY RUNNING:
   `docker build --build-arg=FRAPPE_PATH=https://github.com/frappe/frappe --build-arg=FRAPPE_BRANCH=version-16 --secret=id=apps_json,src=apps.json --build-arg=CACHE_BUST=$(date +%s) --tag=fa-apps:v0.6.0-preview --file=images/layered/Containerfile .`
   (~28 min) then `docker compose -p fa up -d` recreates onto the new image.
   NOTE: current frappe_docker uses the **secret mount**, not APPS_JSON_BASE64 —
   the base64 build-arg is dead, docs forbid it.

After ANY migrate or code swap while a human browses: `bench --site <site>
clear-cache` AND a hard browser reload — sidebar/boot data is site-cached AND
client-cached (`auto_generate_sidebar_from_module` is @site_cache).

## Hard-won rules

- One heavy docker operation at a time. Never `docker system prune`. Never touch
  techmaze-*/openconstructionerp-* containers (other projects).
- compose YAML: don't use folded scalars (`>`) for multi-flag bench commands —
  the fold silently split `new-site` flags into separate commands. And
  `set-config -p allow_tests true` crashes (literal_eval wants `True`);
  plain `set-config allow_tests true` per site is fine.
- pkill in containers: bracket the pattern `[b]ench` AND make sure no other
  unbracketed copy of the pattern appears in the SAME command line (an
  unbracketed pgrep later in the line self-killed the shell once).
- Sidebar looks wrong after app work? Check `tabWorkspace Sidebar`.app is
  "frappe_agents" — list views filter sidebars by app and fall back to the
  auto module sidebar ("Frappe Agents", hammer icon) when it's empty.

Last verified: 2026-08-16 — stack built, 759 tests green on clean_test, UAE demo
site live, sidebar fix applied via method 1.
