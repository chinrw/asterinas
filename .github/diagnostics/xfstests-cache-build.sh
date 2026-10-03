#!/bin/bash
# SPDX-License-Identifier: MPL-2.0

set -euo pipefail

validation_dir=/opt/xfstests-validation
source_dir=$validation_dir/source
evidence_dir=$validation_dir/evidence
baseline_roots=/nix/var/nix/gcroots/xfstests-validation-base
mkdir -p "$source_dir/test" "$source_dir/distro" "$evidence_dir" "$baseline_roots"
printf '%s\n' "$SOURCE_REVISION" > "$evidence_dir/source-revision.txt"

phase() {
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" "$*" \
        | tee -a "$evidence_dir/phases.log"
}

# The published image intentionally retains some unrooted build inputs. Preserve
# its original store so this experiment measures only the newly added packages.
phase baseline-store-start
nix --extra-experimental-features nix-command path-info --all \
    | sort -u > "$evidence_dir/base-store-paths.txt"
while IFS= read -r store_path; do
    ln -s "$store_path" "$baseline_roots/${store_path##*/}"
done < "$evidence_dir/base-store-paths.txt"
phase baseline-store-finish

cp -a /mnt/source/test/initramfs "$source_dir/test/"
cp /mnt/source/distro/nixpkgs.nix "$source_dir/distro/"
cd "$source_dir"
nixfmt --check test/initramfs/nix/default.nix test/initramfs/nix/conformance/xfstests.nix

package_metadata() {
    nix-instantiate --eval --strict --json --expr '
      let
        packages = import /opt/xfstests-validation/source/test/initramfs/nix {
          target = "x86_64";
        };
        pkgs = import /opt/xfstests-validation/source/distro/nixpkgs.nix {
          system = builtins.currentSystem;
          crossSystem.config = "x86_64-unknown-linux-gnu";
        };
        describe = p: { output = p.outPath; drv = p.drvPath; };
      in {
        wrapper = describe packages.conformance.xfstests;
        raw = describe packages.conformance.xfstests.unwrapped;
        coreutils = describe (pkgs.coreutils.override { singleBinary = false; });
      }
    '
}

package_metadata > "$evidence_dir/packages.json"
raw_output=$(jq -r .raw.output "$evidence_dir/packages.json")
raw_drv=$(jq -r .raw.drv "$evidence_dir/packages.json")
wrapper_output=$(jq -r .wrapper.output "$evidence_dir/packages.json")
wrapper_drv=$(jq -r .wrapper.drv "$evidence_dir/packages.json")
coreutils_output=$(jq -r .coreutils.output "$evidence_dir/packages.json")
coreutils_drv=$(jq -r .coreutils.drv "$evidence_dir/packages.json")
test "$raw_drv" = /nix/store/7gg44iq87pnym9rnp773jxgd56ip499f-xfstests-2026.06.21.drv
test "$coreutils_drv" = /nix/store/kaazcbigz6v0mwqwgh4sb9p4i333ym5c-coreutils-9.11.drv
for package_output in "$raw_output" "$wrapper_output" "$coreutils_output"; do
    if grep -Fqx "$package_output" "$evidence_dir/base-store-paths.txt"; then
        printf 'Expected a missing baseline output, found %s\n' "$package_output" >&2
        exit 1
    fi
done
test "$(nix-instantiate test/initramfs/nix -A conformance.xfstests)" = "$wrapper_drv"
nix-store --query --references "$wrapper_drv" > "$evidence_dir/wrapper-inputs.txt"
grep -Fx "$raw_drv" "$evidence_dir/wrapper-inputs.txt"
grep -Fx "$coreutils_drv" "$evidence_dir/wrapper-inputs.txt"

# passthru must expose the original package without changing the guest wrapper.
cp test/initramfs/nix/conformance/xfstests.nix "$validation_dir/xfstests.nix.original"
sed -i '/^  passthru\.unwrapped = xfstests;$/d' test/initramfs/nix/conformance/xfstests.nix
baseline_wrapper_drv=$(nix-instantiate test/initramfs/nix -A conformance.xfstests)
printf '%s\n' "$baseline_wrapper_drv" > "$evidence_dir/baseline-wrapper-drv.txt"
test "$baseline_wrapper_drv" = "$wrapper_drv"
mv "$validation_dir/xfstests.nix.original" test/initramfs/nix/conformance/xfstests.nix

# Compare the actual Make commands so this check covers the standalone target.
python3 - "$evidence_dir" <<'PY_CHECK'
from pathlib import Path
import shlex
import subprocess
import sys

root = Path(sys.argv[1])
makefile = Path('test/initramfs/Makefile')
original = makefile.read_text()
removable = ['\t\t-A conformance.xfstests \\\n', '\t\t-A conformance.xfstests.unwrapped \\\n']
before = original
for line in removable:
    if before.count(line) != 1:
        raise SystemExit('Expected exactly one new prebuild entry.')
    before = before.replace(line, '')
previous = makefile.with_name('Makefile.before')
previous.write_text(before)
try:
    for arch in ('x86_64', 'riscv64', 'aarch64'):
        commands = {}
        for label, name in (('before', 'Makefile.before'), ('after', 'Makefile')):
            command = subprocess.check_output(
                ['make', '--no-print-directory', '-C', 'test/initramfs', '-f', name, '-n', f'{arch}_pkgs'], text=True)
            (root / f'make-{arch}-{label}.txt').write_text(command)
            commands[label] = shlex.split(command.replace('\\\n', ' '))
        expected = commands['after'].copy()
        if arch == 'x86_64':
            for attribute in ('conformance.xfstests', 'conformance.xfstests.unwrapped'):
                index = expected.index(attribute)
                assert expected[index - 1] == '-A'
                del expected[index - 1:index + 1]
        assert commands['before'] == expected, f'Unexpected command change for {arch}'
finally:
    previous.unlink()
PY_CHECK

phase prebuild-start
make -C test/initramfs x86_64_pkgs 2>&1 | tee "$evidence_dir/prebuild.log"
phase prebuild-finish

collect_garbage() {
    # Base derivation roots must not keep newly built outputs alive indirectly.
    nix-collect-garbage -d --option keep-outputs false 2>&1 | tee "$1"
}

verify_retention() {
    local suffix=$1
    for package in raw wrapper coreutils; do
        local package_output
        package_output=$(jq -r ".$package.output" "$evidence_dir/packages.json")
        nix-store --check-validity "$package_output"
        nix-store --query --roots "$package_output" > "$evidence_dir/roots-$package-$suffix.txt"
        nix --extra-experimental-features nix-command path-info --json --closure-size "$package_output" \
            > "$evidence_dir/size-$package-$suffix.json"
    done
    nix-store --query --requisites "$wrapper_output" > "$evidence_dir/wrapper-closure-$suffix.txt"
    grep -Fx "$coreutils_output" "$evidence_dir/wrapper-closure-$suffix.txt"
}

phase first-gc-start
collect_garbage "$evidence_dir/first-gc.log"
verify_retention after-gc
phase first-gc-finish

phase wrapper-mutation-start
wrapper_source=test/initramfs/src/conformance/xfstests/run_xfstests.sh
cp "$wrapper_source" "$validation_dir/run_xfstests.sh.original"
printf '\n# Validation-only wrapper source change.\n' >> "$wrapper_source"
package_metadata > "$evidence_dir/mutated-packages.json"
test "$(jq -c .raw "$evidence_dir/packages.json")" = "$(jq -c .raw "$evidence_dir/mutated-packages.json")"
test "$(jq -c .coreutils "$evidence_dir/packages.json")" = "$(jq -c .coreutils "$evidence_dir/mutated-packages.json")"
test "$(jq -r .wrapper.drv "$evidence_dir/mutated-packages.json")" != "$wrapper_drv"
nix-build test/initramfs/nix -A conformance.xfstests --no-out-link --dry-run \
    --option substituters '' 2>&1 | tee "$evidence_dir/mutated-dry-run.log"
if grep -F -e "$raw_drv" -e "$coreutils_drv" "$evidence_dir/mutated-dry-run.log"; then
    printf 'The wrapper source change would rebuild an expensive dependency.\n' >&2
    exit 1
fi
nix-build test/initramfs/nix -A conformance.xfstests --no-out-link \
    --option substituters '' 2>&1 | tee "$evidence_dir/mutated-build.log"
mv "$validation_dir/run_xfstests.sh.original" "$wrapper_source"
package_metadata > "$evidence_dir/restored-packages.json"
cmp "$evidence_dir/packages.json" "$evidence_dir/restored-packages.json"
phase wrapper-mutation-finish

phase final-gc-start
collect_garbage "$evidence_dir/final-gc.log"
verify_retention final
nix --extra-experimental-features nix-command path-info --all \
    | sort -u > "$evidence_dir/final-store-paths.txt"
comm -13 "$evidence_dir/base-store-paths.txt" "$evidence_dir/final-store-paths.txt" \
    > "$evidence_dir/added-store-paths.txt"
comm -23 "$evidence_dir/base-store-paths.txt" "$evidence_dir/final-store-paths.txt" \
    > "$evidence_dir/missing-base-store-paths.txt"
test ! -s "$evidence_dir/missing-base-store-paths.txt"
phase final-gc-finish

# Preserve metrics, but the candidate must evaluate the performance job's checkout.
rm -rf "$source_dir"
phase validation-complete
