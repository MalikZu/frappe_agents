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


## Notes are WRITTEN, not generated (house rule, 2026-08-16)

The version is computed; the notes are authored. semantic-release still decides
the number and creates the tag — then the generated body gets REPLACED. Never
ship the commit list as the body: commit subjects are written for reviewers at
merge time, not users at upgrade time.

1. BEFORE dispatching, draft the notes from the ROADMAP Done entry + PR bodies
   into a file (scratchpad), using exactly this template:

   <one sentence: what this release IS>

   ### New
   - <what the user can now do, where to find it — user words, one line each>

   ### After you upgrade        <- mandatory; the section users actually need
   - <what bench migrate does to their site, in effect-words>
   - <what to enable/configure to benefit>
   - <anything that behaves differently>

   ### Fixed
   - <one human sentence per fix a user could have noticed>

   <details><summary>Full changelog</summary> compare link + generated list </details>

   Hard rules: no commit subjects in the body; every line user-facing; breaking
   changes go FIRST, bold, before New; patch releases = intro line + Fixed only;
   screenshots when the change is visible.

2. Show the maintainer the draft (30-second read) before anything is public.

3. Dry-run -> dispatch -> then IMMEDIATELY:
   gh release edit vX.Y.Z --notes-file <the draft>

Reference rewrite in this style: the v0.6.0 notes (see the release page after
2026-08-16, or artifact 3c382c95). Style models: esbuild (prose+examples),
Biome (user-facing sentences, generated list demoted).

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
