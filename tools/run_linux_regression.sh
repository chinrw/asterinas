#!/bin/bash

# SPDX-License-Identifier: MPL-2.0

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bundle=
selected_group=${REGRESSION_GROUP:-}
selected_case=${REGRESSION_CASE:-}
exclusions_file=$repo_root/test/initramfs/linux-regression-exclusions.tsv
include_excluded=${REGRESSION_INCLUDE_EXCLUDED:-false}
guest_mode=${REGRESSION_GUEST_MODE:-}
plan_only=false
shard_count=1
shard_index=0
output_dir=$repo_root/test/initramfs/build/linux-regression
accel=kvm
case_timeout=180

usage() {
    cat <<'EOF'
Usage: run_linux_regression.sh [OPTIONS]

Run registered Linux regression workloads, sharing one VM per subsystem by default.

Options:
  --bundle PATH      Reuse a previously built Linux regression bundle
  --mode MODE        subsystem (default) or case for a fresh VM per workload
  --plan-only        Write the effective workload manifest without starting guests
  --shard-count N    Distribute workloads across N shards (default: 1)
  --shard-index I    Run the zero-based shard I (default: 0)
  --group NAME       Run one group instead of the complete suite
  --case NUMBER      Run one workload in the selected group
  --exclusions PATH  Use this temporary Linux exclusions table
  --include-excluded Run workloads listed in that table as well
  --output PATH      Write logs and results here (must be empty)
  --accel MODE       QEMU accelerator: kvm (default) or tcg
  --timeout SECONDS  Time limit for each workload (default: 180)
  -h, --help         Show this help message

Set QEMU to select a different qemu-system-x86_64 executable.
EOF
}

while (($#)); do
    case "$1" in
        --bundle|--mode|--shard-count|--shard-index|--group|--case|--exclusions|--output|--accel|--timeout)
            [[ $# -ge 2 ]] || { echo "missing argument for $1" >&2; exit 2; }
            case "$1" in
                --bundle) bundle=$2 ;;
                --mode) guest_mode=$2 ;;
                --shard-count) shard_count=$2 ;;
                --shard-index) shard_index=$2 ;;
                --group) selected_group=$2 ;;
                --case) selected_case=$2 ;;
                --exclusions) exclusions_file=$2 ;;
                --output) output_dir=$2 ;;
                --accel) accel=$2 ;;
                --timeout) case_timeout=$2 ;;
            esac
            shift 2
            ;;
        --plan-only) plan_only=true; shift ;;
        --include-excluded) include_excluded=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ -z $guest_mode || $guest_mode == subsystem || $guest_mode == case ]] || { echo "Expected --mode subsystem or case" >&2; exit 2; }
[[ $accel == kvm || $accel == tcg ]] || { echo "invalid accelerator: $accel" >&2; exit 2; }
[[ $case_timeout =~ ^[1-9][0-9]*$ ]] || { echo "timeout must be a positive number of seconds" >&2; exit 2; }
[[ $include_excluded == true || $include_excluded == false ]] || { echo "Invalid exclusion setting" >&2; exit 2; }
if [[ ! $shard_count =~ ^[1-9][0-9]{0,2}$ || ! $shard_index =~ ^(0|[1-9][0-9]{0,2})$ ]] ||
    ((shard_count > 256 || shard_index >= shard_count)); then
    echo "Expected 1 to 256 shards and a zero-based index below the shard count." >&2
    exit 2
fi
if [[ -n $selected_case ]]; then
    [[ -n $selected_group && $selected_case =~ ^[1-9][0-9]*$ ]] || {
        echo "--case requires --group and a positive workload number" >&2
        exit 2
    }
fi
if [[ $plan_only == false && $accel == kvm && (! -r /dev/kvm || ! -w /dev/kvm) ]]; then
    echo "KVM is unavailable; use --accel tcg to request software emulation" >&2
    exit 1
fi

mkdir -p "$output_dir"
[[ -z $(find "$output_dir" -mindepth 1 -maxdepth 1 -print -quit) ]] || {
    echo "output directory must be empty: $output_dir" >&2
    exit 1
}
output_dir=$(realpath "$output_dir")

scratch=$(mktemp -d)
vm_pid=
tee_pid=
results_file=
selected_rows=()
declare -A completed_cases=()
record_result() {
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$@" >> "$results_file"
    completed_cases[$1/$2]=1
    printf '%s/%s: %s (test=%s, qemu=%s)\n' "$1" "$2" "$3" "$4" "$5"
}
cleanup() {
    status=$?
    trap - EXIT INT TERM
    set +e
    if [[ -n $vm_pid ]]; then
        # GNU timeout owns this group and escalates TERM with --kill-after.
        kill -TERM -- "-$vm_pid" "$vm_pid" 2>/dev/null || true
        wait "$vm_pid" 2>/dev/null || true
        kill -KILL -- "-$vm_pid" 2>/dev/null || true
    fi
    if [[ -n $tee_pid ]]; then
        kill "$tee_pid" 2>/dev/null || true
        wait "$tee_pid" 2>/dev/null || true
    fi
    if [[ -n $results_file ]]; then
        # Subsystem guests write completed cases before proceeding to the next group.
        while IFS=$'\t' read -r group number _; do
            completed_cases[$group/$number]=1
        done < "$results_file"
        for row in "${selected_rows[@]}"; do
            IFS=$'\t' read -r group number action command reason <<< "$row"
            if [[ ! -v completed_cases[$group/$number] ]]; then
                result=NOT_RUN
                reason="runner stopped before workload started"
                if [[ -f $output_dir/$group/$number.log ]] && \
                    grep -aFq "REGRESSION_CASE_START $group $number" "$output_dir/$group/$number.log"; then
                    result=FAIL
                    reason="runner stopped before collecting workload completion"
                fi
                record_result "$group" "$number" "$result" missing missing 0 "$command" "$reason"
            fi
        done
    fi
    rm -rf "$scratch"
    return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ -z $bundle ]]; then
    bundle=$(nix-build "$repo_root/test/initramfs/nix/linux-regression.nix" --out-link "$scratch/bundle" \
        2> >(tee "$output_dir/build.log" >&2))
fi
bundle=$(realpath "$bundle")
for file in bzImage initramfs.cpio.gz groups cases.tsv kernel.config; do
    [[ -s $bundle/$file ]] || { echo "missing bundle file: $bundle/$file" >&2; exit 1; }
done
# Existing bundles predate the subsystem serial protocol and support case mode.
if [[ -z $guest_mode ]]; then
    guest_mode=case
    if [[ -f $bundle/subsystem-v1 ]]; then guest_mode=subsystem; fi
elif [[ $guest_mode == subsystem && ! -f $bundle/subsystem-v1 && $plan_only == false ]]; then
    echo "This bundle predates subsystem mode; rebuild it or use --mode case." >&2
    exit 2
fi
export PATH="$bundle/host-tools/bin:$PATH"
qemu=${QEMU:-qemu-system-x86_64}
# The shared x86-64 regression scenarios currently assume four-level paging.
cpu=host,la57=off
[[ $accel == kvm ]] || cpu=max,la57=off

[[ -r $exclusions_file ]] || { echo "missing Linux exclusions table: $exclusions_file" >&2; exit 1; }
IFS= read -r exclusions_header < "$exclusions_file"
[[ $exclusions_header == $'group\tcommand\tcategory\treason\trestore_when' ]] || {
    echo "invalid Linux exclusions table header" >&2
    exit 1
}
exclusion_keys=()
exclusion_categories=()
exclusion_reasons=()
exclusion_restore=()
exclusion_matches=()
while IFS= read -r row || [[ -n $row ]]; do
    [[ -n $row && $row != \#* ]] || continue
    IFS=$'\t' read -r exclusion_group exclusion_command category exclusion_reason restore_when extra <<< "$row"
    [[ $exclusion_group =~ ^[a-z][a-z0-9_]*$ && -n $exclusion_command && \
        -n $category && -n $exclusion_reason && -n $restore_when && -z $extra ]] || {
        echo "invalid Linux exclusion: $row" >&2
        exit 1
    }
    key=$exclusion_group$'\t'${exclusion_command% }
    for existing in "${exclusion_keys[@]}"; do
        [[ $existing != "$key" ]] || { echo "duplicate Linux exclusion: $row" >&2; exit 1; }
    done
    exclusion_keys+=("$key")
    exclusion_categories+=("$category")
    exclusion_reasons+=("$exclusion_reason")
    exclusion_restore+=("$restore_when")
    exclusion_matches+=(0)
done < <(tail -n +2 "$exclusions_file")

mapfile -t groups < "$bundle/groups"
[[ ${#groups[@]} -gt 0 ]] || { echo "no regression groups found" >&2; exit 1; }
declare -A seen_groups=()
for group in "${groups[@]}"; do
    [[ $group =~ ^[a-z][a-z0-9_]*$ && ! -v seen_groups[$group] ]] || {
        echo "invalid or duplicate regression group: $group" >&2
        exit 1
    }
    seen_groups[$group]=1
done
if [[ -n $selected_group ]]; then
    [[ $selected_group =~ ^[a-z][a-z0-9_]*$ && -v seen_groups[$selected_group] ]] || {
        echo "unknown regression group: $selected_group" >&2
        exit 2
    }
fi

IFS= read -r header < "$bundle/cases.tsv"
[[ $header == $'group\tcase\taction\tcommand\treason' ]] || {
    echo "invalid workload manifest header" >&2
    exit 1
}
declare -A seen_cases=() group_cases=()
while IFS= read -r row || [[ -n $row ]]; do
    IFS=$'\t' read -r group number action command reason <<< "$row"
    [[ $group =~ ^[a-z][a-z0-9_]*$ && -v seen_groups[$group] && \
        $number =~ ^[1-9][0-9]*$ && ! -v seen_cases[$group/$number] && \
        ($action == RUN || $action == SKIP) && -n $command ]] || {
        echo "invalid or duplicate workload: $row" >&2
        exit 1
    }
    [[ $action != SKIP || -n $reason ]] || { echo "missing exclusion reason: $row" >&2; exit 1; }
    seen_cases[$group/$number]=1
    group_cases[$group]=1
    key=$group$'\t'${command% }
    for index in "${!exclusion_keys[@]}"; do
        [[ ${exclusion_keys[index]} == "$key" ]] || continue
        # A workload can also require a feature missing from this kernel image.
        if [[ $action == SKIP && $reason == "[kernel config]"* ]]; then
            exclusion_matches[index]=$((exclusion_matches[index] + 1))
            continue
        fi
        [[ $action == RUN ]] || { echo "temporary exclusion already skipped by the profile: $command" >&2; exit 1; }
        exclusion_matches[index]=$((exclusion_matches[index] + 1))
        if [[ $include_excluded == false ]]; then
            action=SKIP
            reason="[${exclusion_categories[index]}] ${exclusion_reasons[index]} Restore when: ${exclusion_restore[index]}"
        fi
    done
    [[ -z $selected_group || $group == "$selected_group" ]] || continue
    [[ -z $selected_case || $number == "$selected_case" ]] || continue
    selected_rows+=("$group"$'\t'"$number"$'\t'"$action"$'\t'"$command"$'\t'"$reason")
done < <(tail -n +2 "$bundle/cases.tsv")
for index in "${!exclusion_keys[@]}"; do
    [[ ${exclusion_matches[index]} == 1 ]] || {
        echo "Linux exclusion must match exactly one registered workload: ${exclusion_keys[index]}" >&2
        exit 1
    }
done
for group in "${groups[@]}"; do
    [[ -v group_cases[$group] ]] || { echo "no workloads registered for $group" >&2; exit 1; }
done
[[ ${#selected_rows[@]} -gt 0 ]] || { echo "no matching workloads" >&2; exit 2; }

kernel_console=ttyS0
if [[ -f $bundle/separate-kernel-console ]]; then kernel_console=ttyS2; fi
printf 'kernel_console=%s\n' "$kernel_console" > "$output_dir/environment.txt"

if [[ $plan_only == true ]]; then
    printf '%s\n' "$header" "${selected_rows[@]}" > "$output_dir/cases.tsv"
    exit 0
fi

if ((shard_count > 1)); then
    printf '%s\n' "$header" "${selected_rows[@]}" > "$scratch/selected.tsv"
    python3 "$repo_root/tools/linux_regression_groups.py" select "$scratch/selected.tsv" \
        "$guest_mode" "$shard_count" "$shard_index" > "$scratch/shard.tsv"
    mapfile -t selected_rows < "$scratch/shard.tsv"
fi

{
    printf 'source_revision=%s\nbundle=%s\naccelerator=%s\n' \
        "$(git -C "$repo_root" rev-parse HEAD)" "$bundle" "$accel"
    printf 'include_excluded=%s\ncpu_profile=%s\n' "$include_excluded" "$cpu"
    printf 'guest_mode=%s\n' "$guest_mode"
    uname -a
    "$qemu" --version
    sha256sum "$repo_root/tools/run_linux_regression.sh" "$exclusions_file"
    sha256sum "$bundle/bzImage" "$bundle/initramfs.cpio.gz"
} >> "$output_dir/environment.txt"
cp "$bundle/kernel.config" "$output_dir/kernel.config"
cp "$bundle/cases.tsv" "$output_dir/registered-cases.tsv"
cp "$exclusions_file" "$output_dir/linux-exclusions.tsv"
{
    printf '%s\n' "$header"
    if ((${#selected_rows[@]})); then
        printf '%s\n' "${selected_rows[@]}"
    fi
} > "$output_dir/cases.tsv"
if [[ -f $bundle/excluded ]]; then
    cp "$bundle/excluded" "$output_dir/excluded"
    cat "$output_dir/excluded"
fi
results_file=$output_dir/results.tsv
printf 'group\tcase\tresult\ttest_status\tqemu_status\tduration_seconds\tcommand\treason\n' > "$results_file"

run_count=0
for row in "${selected_rows[@]}"; do
    IFS=$'\t' read -r group number action command reason <<< "$row"
    if [[ $action == SKIP ]]; then
        record_result "$group" "$number" SKIP - - 0 "$command" "$reason"
    else
        run_count=$((run_count + 1))
    fi
done
if [[ $run_count -eq 0 ]]; then
    [[ -n $selected_case || $shard_count -gt 1 ]] || { echo "no runnable workloads selected" >&2; exit 1; }
    exit 0
fi

# Each VM uses temporary QEMU snapshots, so tests never change these base images.
for disk in ext2 aux nvme; do
    truncate -s 2G "$scratch/$disk.img"
    mke2fs -q -t ext2 -F "$scratch/$disk.img"
done
truncate -s 512M "$scratch/exfat.img"
mkfs.exfat "$scratch/exfat.img" > "$output_dir/mkfs-exfat.log"

boot_args="console=$kernel_console rdinit=/linux-init lsm=integrity net.ifnames=0 panic=1"
qemu_args=(
    -machine q35 -accel "$accel" -cpu "$cpu" -smp 4 -m 2048
    -nodefaults -no-reboot -display none -monitor none
    -device VGA,bus=pcie.0,addr=0x1
    -kernel "$bundle/bzImage" -initrd "$bundle/initramfs.cpio.gz"
    -serial stdio -snapshot
    -drive "if=none,format=raw,id=ext2,file=$scratch/ext2.img"
    -device virtio-blk-pci,bus=pcie.0,addr=0x6,drive=ext2
    -drive "if=none,format=raw,id=exfat,file=$scratch/exfat.img"
    -device virtio-blk-pci,bus=pcie.0,addr=0x7,drive=exfat
    -drive "if=none,format=raw,id=aux,file=$scratch/aux.img"
    -device virtio-blk-pci,bus=pcie.0,addr=0x8,drive=aux
    -drive "if=none,format=raw,id=nvme,file=$scratch/nvme.img"
    -device nvme,bus=pcie.0,addr=0x4,drive=nvme,serial=regression
    -netdev user,id=net0 -device virtio-net-pci,netdev=net0
    -object rng-random,id=rng0,filename=/dev/urandom
    -device virtio-rng-pci,rng=rng0
)

if [[ $guest_mode == subsystem ]]; then
    TMPDIR="$scratch" python3 "$repo_root/tools/linux_regression_groups.py" run \
        "$output_dir/cases.tsv" "$output_dir" "$case_timeout" "$qemu" "${qemu_args[@]}" -append "$boot_args" &
    vm_pid=$!
    status=0
    wait "$vm_pid" || status=$?
    vm_pid=
    exit "$status"
fi

failed=0
mkfifo "$scratch/qemu-log"
for row in "${selected_rows[@]}"; do
    IFS=$'\t' read -r group number action command reason <<< "$row"
    [[ $action == RUN ]] || continue
    echo "Running Linux regression workload: $group/$number ($command)"
    mkdir -p "$output_dir/$group"
    record=$output_dir/$group/$number.result
    log_file=$output_dir/$group/$number.log
    kernel_serial=()
    if [[ $kernel_console == ttyS2 ]]; then
        kernel_serial=(-serial "file:$output_dir/$group/$number.kernel.log")
    fi
    started=$SECONDS
    tee "$log_file" < "$scratch/qemu-log" &
    tee_pid=$!
    TMPDIR="$scratch" timeout --kill-after=10 "$case_timeout" "$qemu" "${qemu_args[@]}" \
        -append "$boot_args regression_group=$group regression_case=$number" -serial "file:$record" "${kernel_serial[@]}" \
        > "$scratch/qemu-log" 2>&1 &
    vm_pid=$!
    if wait "$vm_pid"; then
        qemu_status=0
    else
        qemu_status=$?
    fi
    if ! wait "$tee_pid"; then
        qemu_status=1
    fi
    vm_pid=
    tee_pid=

    result=FAIL
    test_status=missing
    reason="missing or invalid completion record"
    start_count=$(tr -d '\r' < "$log_file" | grep -aFxc "REGRESSION_CASE_START $group $number" || true)
    if [[ -f $record ]]; then
        mapfile -t records < <(tr -d '\r' < "$record")
        if [[ ${#records[@]} == 1 ]]; then
            if read -r tag result_group result_case test_status extra <<< "${records[0]}" && \
                [[ $tag == LINUX_REGRESSION_RESULT && $result_group == "$group" && \
                $result_case == "$number" && $test_status =~ ^[0-9]+$ && -z $extra ]]; then
                reason="workload failed"
                if [[ $test_status == 0 && $qemu_status == 0 && $start_count == 1 ]]; then
                    result=PASS
                    reason=
                fi
            fi
        fi
    fi
    if [[ $start_count == 0 ]]; then
        result=NOT_RUN
        if [[ $qemu_status == 124 || $qemu_status == 137 ]]; then
            reason="VM timed out before workload started"
        elif [[ $qemu_status != 0 ]]; then
            reason="QEMU or console logging failed before workload started"
        else
            reason="guest exited before workload started"
        fi
    elif [[ $start_count != 1 ]]; then
        result=FAIL
        reason="duplicate workload start records"
    elif [[ $qemu_status == 124 || $qemu_status == 137 ]]; then
        reason="workload VM timed out"
    elif [[ $qemu_status != 0 ]]; then
        reason="QEMU or console logging failed"
    elif ! tr -d '\r' < "$log_file" | grep -axF "REGRESSION_CONSOLE_END $group $number" > /dev/null; then
        result=FAIL
        reason="incomplete guest console log"
    fi
    record_result "$group" "$number" "$result" "$test_status" "$qemu_status" \
        "$((SECONDS - started))" "$command" "$reason"
    [[ $result == PASS ]] || failed=1
done

echo "Linux regression results: $output_dir/results.tsv"
exit "$failed"
