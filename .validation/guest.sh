#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
set -eu
cd /opt/xfstests
export PATH="$(sed -n 's/^export PATH=//p' run_xfstests.sh)"
export CONFORMANCE_TEST_SELECTOR= XFSTESTS_FS_TYPE=tmpfs
export HOST_OPTIONS=/opt/xfstests/tmpfs/config/xfstests.config
export XFSTESTS_DIR=/opt/xfstests
. "$HOST_OPTIONS"
. /opt/xfstests/tmpfs/prepare.sh
bash_path=$(command -v bash)
baseline=./loader_validation/check.baseline
fixed=./check
work=/tmp/loader-validation
mkdir -p "$work"
cleanup() {
    ln -sfn /proc/self/fd /dev/fd
    umount /opt/xfstests/test 2>/dev/null || true
    umount /opt/xfstests/scratch 2>/dev/null || true
}
trap cleanup EXIT
run() {
    label=$1; expected=$2; runner=$3; shift 3
    rc=0
    "$bash_path" "$runner" -n "$@" generic/001 generic/558 generic/590 > "$work/$label.log" 2>&1 || rc=$?
    cat "$work/$label.log"
    if [ "$expected" = success ]; then test "$rc" -eq 0; else test "$rc" -ne 0; fi
    echo "CASE_STATUS $label $rc"
}
selection() {
    log=$1; first=$2; second=$3
    grep -Eq '^generic/001[[:space:]]*$' "$log"
    for pair in "558:$first" "590:$second"; do
        id=${pair%:*}; state=${pair#*:}
        if [ "$state" = blocked ]; then
            grep -Eq "^generic/$id[[:space:]]+\[expunged\]" "$log"
        else
            grep -Eq "^generic/$id[[:space:]]*$" "$log"
        fi
    done
}
# Every upstream invocation above forces -n and an empty environment selector.
# These checks still prepare isolated tmpfs mounts but never run test bodies.
test ! -e /dev/fd && test ! -L /dev/fd
run baseline-missing success "$baseline" -E tmpfs/run_list/block.list
selection "$work/baseline-missing.log" allowed allowed
grep -q '/dev/fd/' "$work/baseline-missing.log"
run fixed-missing success "$fixed" -E tmpfs/run_list/block.list
selection "$work/fixed-missing.log" blocked blocked
run unchanged-e success "$fixed" -e generic/558,generic/590
selection "$work/unchanged-e.log" allowed allowed
grep -q '/dev/fd/' "$work/unchanged-e.log"
echo LOADER_MISSING_FD_COMPARISON_PASS
ln -s /proc/self/fd /dev/fd
run baseline-linked success "$baseline" -E tmpfs/run_list/block.list
selection "$work/baseline-linked.log" blocked blocked
run fixed-linked success "$fixed" -E tmpfs/run_list/block.list
selection "$work/fixed-linked.log" blocked blocked
echo LOADER_LINKED_COMPARISON_PASS
printf '# comment\ngeneric/558# trailing comment\n\ngeneric/590' > "$work/comments.list"
run comments success "$fixed" -E "$work/comments.list"
selection "$work/comments.log" blocked blocked
printf 'generic/558\n' > "$work/first.list"
printf 'generic/590\n' > "$work/second.list"
run repeated success "$fixed" -E "$work/first.list" -E "$work/second.list"
selection "$work/repeated.log" blocked blocked
cp "$work/comments.list" "$work/path with spaces.list"
run spaces success "$fixed" -E "$work/path with spaces.list"
selection "$work/spaces.log" blocked blocked
: > "$work/empty.list"
run empty success "$fixed" -E "$work/empty.list"
selection "$work/empty.log" allowed allowed
printf '# comment\n\n' > "$work/only-comments.list"
run only-comments success "$fixed" -E "$work/only-comments.list"
selection "$work/only-comments.log" allowed allowed
run missing-file success "$fixed" -E "$work/nonexistent.list"
selection "$work/missing-file.log" allowed allowed
awk 'BEGIN { for (i=0;i<8192;i++) print "generic/558"; print "generic/590" }' > "$work/large.list"
run large success "$fixed" -E "$work/large.list"
selection "$work/large.log" blocked blocked
echo LOADER_CONTENT_CASES_PASS
mkdir "$work/shim"
real_sed=$(command -v sed)
printf '#!/bin/sh\nfor arg in "$@"; do\n case "$arg" in\n */producer-error.list) exit 7 ;;\n */producer-partial.list) printf "generic/558\\n"; exit 7 ;;\n esac\ndone\nexec "%s" "$@"\n' "$real_sed" > "$work/shim/sed"
chmod +x "$work/shim/sed"
: > "$work/producer-error.list"
: > "$work/producer-partial.list"
export PATH="$work/shim:$PATH"
run baseline-error success "$baseline" -E "$work/producer-error.list"
selection "$work/baseline-error.log" allowed allowed
run fixed-error failure "$fixed" -E "$work/producer-error.list"
grep -q 'Failed to read exclusion file:' "$work/fixed-error.log"
! grep -q '^generic/' "$work/fixed-error.log"
run baseline-partial success "$baseline" -E "$work/producer-partial.list"
selection "$work/baseline-partial.log" blocked allowed
run fixed-partial failure "$fixed" -E "$work/producer-partial.list"
grep -q 'Failed to read exclusion file:' "$work/fixed-partial.log"
! grep -q '^generic/' "$work/fixed-partial.log"
echo LOADER_PRODUCER_FAILURES_PASS
echo XFSTESTS_LOADER_VALIDATION_PASS
