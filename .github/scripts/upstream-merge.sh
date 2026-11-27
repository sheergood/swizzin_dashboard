#!/usr/bin/env bash
# Merge the original project's latest commits into a scratch test branch of this fork.
# Never touches the fork's real branches; the workflow promotes the result only after CI passes.
#
# env: UPSTREAM_URL UPSTREAM_BRANCH BASE TEST_BRANCH FORCE_TEST(true|false)
# writes to $GITHUB_OUTPUT: status (up-to-date|merged|conflict|workflow-changes), sha, count, base_sha,
#                           commits (markdown list), files (markdown list)
set -euo pipefail

out() { # out name value  (value may be multi-line)
    local d="EOF_$RANDOM$RANDOM"
    { echo "$1<<$d"; echo "$2"; echo "$d"; } >> "${GITHUB_OUTPUT:-/dev/stdout}"
}

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

git fetch --quiet --no-tags origin "+refs/heads/${BASE}:refs/remotes/origin/${BASE}"
git fetch --quiet --no-tags "$UPSTREAM_URL" "+refs/heads/${UPSTREAM_BRANCH}:refs/remotes/upstream/${UPSTREAM_BRANCH}"
up="upstream/${UPSTREAM_BRANCH}"
name="${UPSTREAM_URL##*github.com/}"; name="${name%.git}"
base_sha=$(git rev-parse "origin/${BASE}")
count=$(git rev-list --count "origin/${BASE}..${up}")
out base_sha "$base_sha"
out count "$count"
out commits "$(git log -n 50 --no-merges --format='- [`%h`]('"${UPSTREAM_URL%.git}"'/commit/%H) %s — %an, %as' "origin/${BASE}..${up}")"

if [ "$count" -eq 0 ] && [ "${FORCE_TEST:-false}" != "true" ]; then
    echo "Already up to date with ${UPSTREAM_URL} ${UPSTREAM_BRANCH}."
    out status up-to-date
    out sha "$base_sha"
    exit 0
fi

git checkout --quiet -B "$TEST_BRANCH" "origin/${BASE}"
if [ "$count" -gt 0 ]; then
    if ! git merge --no-ff --no-edit -m "Merge upstream ${name} ${UPSTREAM_BRANCH} (${count} new commits)" "$up" > /tmp/merge.log 2>&1; then
        cat /tmp/merge.log
        out files "$(git diff --name-only --diff-filter=U | sed 's/^/- `/; s/$/`/')"
        git merge --abort
        echo "Merge conflict; nothing was changed."
        out status conflict
        out sha "$base_sha"
        exit 0
    fi
fi

# The token Actions gives a workflow may not create or change workflow files, so an upstream change
# there has to be merged by hand (or with a personal token).
wf=$(git diff --name-only "origin/${BASE}" HEAD -- .github/workflows)
if [ -n "$wf" ]; then
    out files "$(echo "$wf" | sed 's/^/- `/; s/$/`/')"
    echo "Upstream changed workflow files; needs a manual merge."
    out status workflow-changes
    out sha "$base_sha"
    exit 0
fi

out files "$(git diff --stat "origin/${BASE}" HEAD | tail -1)"
git push --quiet --force origin "HEAD:refs/heads/${TEST_BRANCH}"
out status merged
out sha "$(git rev-parse HEAD)"
echo "Merged ${count} upstream commit(s) into ${TEST_BRANCH} @ $(git rev-parse --short HEAD)."
