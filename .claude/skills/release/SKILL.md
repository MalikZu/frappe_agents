---
name: release
description: Cut a frappe_agents release — dry-run the version and notes first, dispatch release.yml, verify the tag and notes. Use whenever a release, version bump, or tag is asked for.
---

# Release (semantic-release, deliberate dispatch)

Releases are never automatic: `release.yml` is `workflow_dispatch` only.
Run it after the work is merged to main and CI is green.

## Before dispatching

1. Main is green (linters, server tests, harness tests on the merge commit).
2. Dry-run locally to see the version AND the notes body before anything is public:

```bash
git clone /Users/malik/Projects/frappe_agents /tmp/rel && cd /tmp/rel
git remote set-url origin https://github.com/MalikZu/frappe_agents.git
npm install --no-save semantic-release @semantic-release/commit-analyzer \
  @semantic-release/release-notes-generator @semantic-release/exec \
  @semantic-release/git @semantic-release/github \
  conventional-changelog-conventionalcommits@8
GITHUB_TOKEN=$(gh auth token) npx semantic-release --dry-run --no-ci
```

Empty notes body = do not dispatch; fix first (see gotchas).
feat→minor, fix/perf→patch; docs/chore/refactor/test/ci→no release.

3. Re-stamp any skill whose "Last verified" numbers changed (dev-bench test count).

## Dispatch and verify

```bash
gh workflow run release.yml
gh run list --workflow=release.yml -L 1   # grab the id, then: gh run watch <id>
gh release view vX.Y.Z
```

Verify all three: the tag exists, the notes have Features/Fixes sections, and the
`chore(release): bumped to vX.Y.Z [skip ci]` commit is on main.

## Gotchas (learned the hard way)

- `conventional-changelog-conventionalcommits` must stay **pinned to @8** in
  release.yml. Unpinned (v9+), the writer silently classifies nothing and the
  release body is empty — bit v0.2.0 (issue #2); its notes were hand-written.
- `.releaserc` parses as YAML — renaming it to .json breaks the single-quoted
  prepareCmd. Plugin options must be [name, options] pairs.
- A release that shipped with bad notes: `gh release edit vX.Y.Z --notes-file f`.
  Never re-run the workflow for that — the tag already exists.

Last verified: 2026-08-13 — v0.3.0.
