#!/usr/bin/env bash
# Merge the original project's commits into a scratch test branch of this fork, following the rules
# in .github/upstream-sync.conf. Never touches the fork's real branches: the workflow tests the
# result and promotes it only when it's allowed to (see FORK.md, "Upstream updates").
#
# Rules (parameters in .github/upstream-sync.conf):
#   QUARANTINE_DAYS  upstream commits are taken only once they are this many days old, so the
#                    original author's own follow-up fixes and reverts arrive with them
#   REVIEW_ALL       every upstream change waits for your approval (repos that diverge a lot)
#   REVIEW_PATHS     regex of paths that always wait for approval (code `box update` runs everywhere)
#   PROTECTED_APPS   apps you run; a change to any of their scripts waits for approval
#   also always waiting for approval: files this fork has changed, and removed apps
#
# env: UPSTREAM_URL UPSTREAM_BRANCH BASE TEST_BRANCH FORCE_TEST(true|false) APPROVE_SHA(optional)
#      CONF (default .github/upstream-sync.conf), NO_PUSH (tests)
# writes to $GITHUB_OUTPUT:
#   status   up-to-date | quarantined | merged | review | conflict | workflow-changes | bad-approve
#   sha      the commit to test/promote (fork side), upstream (the upstream commit merged),
#   count, base_sha, waiting, waiting_until, commits, files, reasons (markdown)
set -euo pipefail

out() { # out name value  (value may be multi-line)
    local d="EOF_$RANDOM$RANDOM"
    { echo "$1<<$d"; echo "$2"; echo "$d"; } >> "${GITHUB_OUTPUT:-/dev/stdout}"
}

QUARANTINE_DAYS=7
REVIEW_ALL=false
REVIEW_PATHS=''
PROTECTED_APPS=''
conf=${CONF:-.github/upstream-sync.conf}
# shellcheck source=/dev/null
[[ -f $conf ]] && . "$conf"
[[ $QUARANTINE_DAYS =~ ^[0-9]+$ ]] || {
    echo "QUARANTINE_DAYS must be a whole number of days" >&2
    exit 1
}

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

git fetch --quiet --no-tags origin "+refs/heads/${BASE}:refs/remotes/origin/${BASE}"
git fetch --quiet --no-tags "$UPSTREAM_URL" "+refs/heads/${UPSTREAM_BRANCH}:refs/remotes/upstream/${UPSTREAM_BRANCH}"
up="upstream/${UPSTREAM_BRANCH}"
name="${UPSTREAM_URL##*github.com/}"
name="${name%.git}"
base="origin/${BASE}"
base_sha=$(git rev-parse "$base")
out base_sha "$base_sha"

# --- which upstream commit to take
approve=${APPROVE_SHA:-}
if [[ -n $approve ]]; then
    if ! [[ $approve =~ ^[0-9a-f]{7,40}$ ]] || ! target=$(git rev-parse --verify --quiet "${approve}^{commit}") ||
        ! git merge-base --is-ancestor "$target" "$up"; then
        echo "The approved commit ${approve} isn't on ${name} ${UPSTREAM_BRANCH}."
        out status bad-approve
        out sha "$base_sha"
        exit 0
    fi
else
    # newest commit on the upstream branch that is at least QUARANTINE_DAYS old
    target=$(git rev-list -1 --first-parent --before="${QUARANTINE_DAYS} days ago" "$up" || true)
fi
# upstream commits not merged yet and still too young
excl=("^$base")
[[ -n $target ]] && excl+=("^$target")
waiting=$(git rev-list --count "$up" "${excl[@]}")
out waiting "$waiting"
if ((waiting > 0)); then
    oldest=$(git log --format=%ct "$up" "${excl[@]}" | tail -n1)
    out waiting_until "$(date -u -d "@$((oldest + QUARANTINE_DAYS * 86400))" +%F)"
fi
count=0
[[ -n $target ]] && count=$(git rev-list --count "$base..$target")
out count "$count"
out upstream "${target:-}"
[[ -n $target ]] && out commits "$(git log -n 50 --no-merges --format='- [`%h`]('"${UPSTREAM_URL%.git}"'/commit/%H) %s — %an, %as' "$base..$target")"

if [[ $count -eq 0 && ${FORCE_TEST:-false} != "true" ]]; then
    if ((waiting > 0)); then
        echo "${waiting} new upstream commit(s), all younger than ${QUARANTINE_DAYS} days; waiting."
        out status quarantined
    else
        echo "Already up to date with ${UPSTREAM_URL} ${UPSTREAM_BRANCH}."
        out status up-to-date
    fi
    out sha "$base_sha"
    exit 0
fi
[[ -n $target ]] || target=$base_sha

git checkout --quiet -B "$TEST_BRANCH" "$base"
if [[ $count -gt 0 ]]; then
    if ! git merge --no-ff --no-edit -m "Merge upstream ${name} ${UPSTREAM_BRANCH} @ ${target:0:7} (${count} new commits)" "$target" > /tmp/merge.log 2>&1; then
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
wf=$(git diff --name-only "$base" HEAD -- .github/workflows)
if [[ -n $wf ]]; then
    out files "$(echo "$wf" | sed 's/^/- `/; s/$/`/')"
    echo "Upstream changed workflow files; needs a manual merge."
    out status workflow-changes
    out sha "$base_sha"
    exit 0
fi

# --- does this update need your approval?
mb=$(git merge-base "$base" "$target")
changed=$(git diff --name-only "$mb" "$target" || true)  # what upstream changed
ours=$(git diff --name-only "$mb" "$base" || true)       # what this fork changed since the same point
reasons=()
list() { sed 's/^/`/; s/$/`/' | paste -sd, - | sed 's/,/, /g'; }
if [[ $REVIEW_ALL == true && -n $changed ]]; then
    reasons+=("every upstream change to this repository is reviewed (REVIEW_ALL)")
fi
if [[ -n $REVIEW_PATHS ]]; then
    core=$(grep -E "$REVIEW_PATHS" <<< "$changed" || true)
    [[ -n $core ]] && reasons+=("changes code that \`box update\` runs on every server: $(list <<< "$core")")
fi
if [[ -n $PROTECTED_APPS ]]; then
    apps_re=$(tr -s ' ' '\n' <<< "$PROTECTED_APPS" | sed '/^$/d' | paste -sd'|' -)
    mine=$(grep -E "^(scripts/(install|remove|upgrade|update|nginx)/(${apps_re})\.sh|sources/functions/(${apps_re}))$" <<< "$changed" || true)
    [[ -n $mine ]] && reasons+=("changes apps you run: $(list <<< "$mine")")
fi
both=$(comm -12 <(sort -u <<< "$changed") <(sort -u <<< "$ours") | sed '/^$/d' | grep -vE '^(CHANGELOG\.md|\.gitignore)$' || true)
[[ -n $both ]] && reasons+=("changes files this fork has changed too (merged cleanly, but check they still fit): $(list <<< "$both")")
removed=$(git diff --name-only --diff-filter=D "$mb" "$target" -- 'scripts/install/*.sh' | sed 's#scripts/install/##; s#\.sh$##' || true)
[[ -n $removed ]] && reasons+=("removes apps (this fork keeps every app unless you decide otherwise): $(list <<< "$removed")")

out files "$(git diff --stat "$base" HEAD | tail -1)"
if [[ -z ${NO_PUSH:-} ]]; then
    git push --quiet --force origin "HEAD:refs/heads/${TEST_BRANCH}"
fi
out sha "$(git rev-parse HEAD)"
if ((${#reasons[@]})) && [[ -z $approve ]]; then
    out reasons "$(printf -- '- %s\n' "${reasons[@]}")"
    out status review
    echo "Merged ${count} upstream commit(s) into ${TEST_BRANCH}; waits for approval:"
    printf -- '- %s\n' "${reasons[@]}"
    exit 0
fi
((${#reasons[@]})) && out reasons "$(printf -- '- %s (approved)\n' "${reasons[@]}")"
out status merged
echo "Merged ${count} upstream commit(s) into ${TEST_BRANCH} @ $(git rev-parse --short HEAD)."
