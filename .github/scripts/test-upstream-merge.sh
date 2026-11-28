#!/usr/bin/env bash
# CI: the upstream sync rules (.github/scripts/upstream-merge.sh) on throwaway local repositories.
# Each case builds a fork and an "upstream" with commits of a chosen age and content, runs the
# merge script and checks the status it reports (and why).
set -euo pipefail
script=$(cd "$(dirname "$0")" && pwd)/upstream-merge.sh
root=$(mktemp -d)
pass=0

conf() {
    cat > "$1/.github/upstream-sync.conf" << 'EOF'
QUARANTINE_DAYS=7
REVIEW_ALL=false
REVIEW_PATHS='^(scripts/box|setup\.sh|sources/|scripts/update/)'
PROTECTED_APPS="sonarr panel"
EOF
}
commit() { # dir days-ago message (files already changed)
    local when
    when=$(date -u -d "-$2 days" '+%Y-%m-%dT%H:%M:%SZ')
    git -C "$1" add -A
    GIT_AUTHOR_DATE=$when GIT_COMMITTER_DATE=$when git -C "$1" -c user.name=t -c user.email=t@t commit -q -m "$3"
}
# setup <case>: $up (upstream work tree, pushed to $up.git) and $fork (fork clone, origin = $fork.git)
setup() {
    d="$root/$1"
    mkdir -p "$d/up"
    up=$d/up fork=$d/fork
    git -C "$up" init -q -b master
    mkdir -p "$up/scripts/install" "$up/sources/functions" "$up/.github/workflows"
    printf 'line1\nline2\nline3\nline4\nline5\n' > "$up/README.md"
    echo "echo sonarr" > "$up/scripts/install/sonarr.sh"
    echo "echo mylar" > "$up/scripts/install/mylar.sh"
    echo "echo mango" > "$up/scripts/install/mango.sh"
    echo "utils" > "$up/sources/functions/utils"
    echo "name: ci" > "$up/.github/workflows/ci.yml"
    commit "$up" 100 "base"
    git clone -q --bare "$up" "$d/up.git"
    git clone -q --bare "$up" "$d/fork.git"
    git clone -q "$d/fork.git" "$fork"
    mkdir -p "$fork/.github/scripts"
    conf "$fork"
    sed -i 's/line1/line1 (fork)/' "$fork/README.md"
    commit "$fork" 50 "fork change"
    git -C "$fork" push -q origin master
}
upstream_commit() { # days message
    commit "$up" "$1" "$2"
    git -C "$up" push -q "$d/up.git" master
}
run() { # [APPROVE_SHA] -> output file (fresh each run)
    : > "$d/out"
    (cd "$fork" && GITHUB_OUTPUT="$d/out" UPSTREAM_URL="$d/up.git" UPSTREAM_BRANCH=master BASE=master TEST_BRANCH=upstream-sync \
        APPROVE_SHA="${1:-}" FORCE_TEST=false bash "$script" > "$d/log" 2>&1) || { cat "$d/log"; echo "::error::merge script failed"; exit 1; }
}
get() { awk -v k="$1" '$0 ~ "^"k"<<" {d=substr($0, length(k)+3); f=1; next} f && $0==d {f=0; exit} f {print}' "$d/out"; }
expect() { # case status [reason-substring]
    local st
    st=$(get status)
    if [[ $st != "$2" ]]; then
        echo "::error::$1: status $st, expected $2"
        cat "$d/log"
        exit 1
    fi
    if [[ -n ${3:-} ]] && ! get reasons | grep -qF "$3"; then
        echo "::error::$1: reasons don't mention '$3':"
        get reasons
        exit 1
    fi
    echo "ok: $1 -> $st"
    pass=$((pass + 1))
}

setup low
echo "echo mylar 2" > "$up/scripts/install/mylar.sh"
upstream_commit 10 "mylar fix"
run
expect "old change to an app you don't run" merged

setup young
echo "echo mylar 2" > "$up/scripts/install/mylar.sh"
upstream_commit 2 "mylar fix"
run
expect "change younger than the quarantine" quarantined
[[ $(get waiting) == 1 && -n $(get waiting_until) ]] || { echo "::error::waiting/waiting_until missing"; exit 1; }

setup mixed
echo "echo mylar 2" > "$up/scripts/install/mylar.sh"
upstream_commit 10 "old fix"
echo "echo mylar 3" > "$up/scripts/install/mylar.sh"
upstream_commit 1 "new fix"
run
expect "old change merged, young one waits" merged
[[ $(get waiting) == 1 ]] || { echo "::error::the young commit should still be waiting"; exit 1; }
git -C "$fork" log -1 --format=%s upstream-sync | grep -q "1 new commits" || { echo "::error::merged more than the old commit"; exit 1; }

setup core
echo "utils 2" > "$up/sources/functions/utils"
upstream_commit 10 "utils change"
run
expect "change to code box update runs" review "sources/functions/utils"

setup app
echo "echo sonarr 2" > "$up/scripts/install/sonarr.sh"
upstream_commit 10 "sonarr change"
run
expect "change to an app you run" review "scripts/install/sonarr.sh"

setup overlap
sed -i 's/line5/line5 (upstream)/' "$up/README.md"
upstream_commit 10 "readme"
run
expect "clean merge into a file the fork changed" review "README.md"

setup removal
git -C "$up" rm -q scripts/install/mango.sh
upstream_commit 10 "remove mango"
run
expect "upstream removes an app" review "mango"

setup conflict
sed -i 's/line1/line1 (upstream)/' "$up/README.md"
upstream_commit 10 "conflicting readme"
run
expect "conflicting change" conflict

setup workflow
echo "name: ci2" > "$up/.github/workflows/ci.yml"
upstream_commit 10 "workflow"
run
expect "upstream workflow change" workflow-changes

setup approve
echo "echo sonarr 2" > "$up/scripts/install/sonarr.sh"
upstream_commit 10 "sonarr change"
run
expect "approval needed first" review
sha=$(git -C "$up" rev-parse HEAD)
echo "echo sonarr 3" > "$up/scripts/install/sonarr.sh"
upstream_commit 9 "a later sonarr change (not approved)"
run "$sha"
expect "approved commit is merged" merged "(approved)"
[[ $(get upstream) == "$sha" ]] || { echo "::error::approval merged $(get upstream), not the approved $sha"; exit 1; }
run "0000000"
expect "unknown approval" bad-approve
run "$(git -C "$fork" rev-parse HEAD)"
expect "approving a commit that isn't upstream's" bad-approve

setup uptodate
run
expect "nothing new" up-to-date

echo "upstream sync rules ok ($pass cases)"
