# Crate2nix fork experiment

This experiment builds the x86-64 dev kernel as separate Nix crate derivations
and measures reuse on fresh runners in `chinrw/asterinas`.
It requires x86-64 Linux and Nix 2.34.9.
The existing default packages, development shell, and Make commands remain available.

The source baseline is `87d64f30d59355b811b5e6812a8dfc07a7bc57cb`.
The Rust toolchain and nixpkgs retain that baseline's pins.
Crate2nix 0.15.0 is pinned at `7c33e664668faecf7655fa53861d7a80c9e464a2`.
The default kernel features, including `cvm_guest`, remain enabled.
Release profiles, TDX execution, other architectures, coverage, and kernel tests
are outside this experiment's validated scope.

## Build and inspect

Run these commands from the repository root:

```bash
nix develop --command python3 tools/experiments/crate2nix/generate.py --check
nix build .#c2n-exp-host-probe --no-link --print-out-paths
nix build .#c2n-exp-kernel-dev --no-link --print-out-paths \
  --option allow-import-from-derivation false --max-jobs 4 --cores 2
nix eval --json .#legacyPackages.x86_64-linux.c2n-exp.graph > seed-crate-graph.json
```

Use `generate.py` without `--check` to update generated inputs after changing
manifests, OSDK templates, or the toolchain.
The generated base crate is its own Cargo workspace.
Its committed `Cargo.lock` freezes the dependencies used for the Cargo comparison;
it is independent of the root workspace lock, as in the original OSDK build.
An intentional dependency update must update this lock explicitly.

The graph records each crate's role, features, source, derivation, outputs,
and direct dependencies.
To compare it with Cargo, retain the Cargo unit graph and metadata,
then run `compare-graph.py CARGO_UNITS CARGO_METADATA NIX_GRAPH`.
The comparison checks every project's compilation instance and direct dependency;
the separately built standard library is excluded from that instance count.
`check-invalidation-controls.py --output evidence/controls` temporarily changes
a real dependency feature and a target compiler option, regenerates the feature
graph, compares it with Cargo, and verifies that all inputs are restored.
These controls check graph and sysroot invalidation without timing modified configurations.

## Run a prebuilt kernel

Prepare the existing boot images and consume the ELF without compiling it again:

```bash
nix develop --command make -C test/initramfs initramfs-boot-images \
  BENCHMARK=none PREBUILT_ROOT="$PWD/target/c2n-prebuilt"
elf="$(nix build .#c2n-exp-kernel-dev --no-link --print-out-paths)/bin/asterinas-osdk-bin"
nix develop --command bash tools/experiments/crate2nix/boot.sh "$elf" evidence/boot
```

`generate.py` prepares the experiment's native OSDK executable first.
The smoke uses TCG, one CPU, 2 GiB RAM, and the existing `/test/boot_hello.sh`.
It requires the success marker, checks that packaging did not compile a crate,
and verifies that the input ELF's hash did not change.
Pass `hello-world` as the third argument to run the existing C, PIE, and assembly
hello-world regression programs.
The existing image helper reads `/etc/resolv.conf` and requires impure evaluation
for this integration image; the kernel compilation graph uses pure evaluation.

## Measure invalidation

```bash
python3 tools/experiments/crate2nix/run-scenario.py assembler-change \
  --seed-graph seed-crate-graph.json --output evidence/assembler-change
```

Supported scenarios are `same-input`, `docs-only`, `assembler-change`,
`core-change`, `library-change`, and `negative`.
Fixtures append a declaration to a known compiled file and restore it afterward.
The script compares the changed derivations with the seed graph's exact reverse
dependency closure before building.
The negative fixture must fail with `C2N_NEGATIVE_INVALIDATION`.
Use a new output directory for every sample.

Local runs use the populated local store and establish invalidation behavior.
They do not establish remote cache restoration or CI speedups.
`--fresh-runner` rejects preexisting project outputs,
and `--require-substitute` disables both local and remote builders.
The probe mode uses `--probe` and the `host-graph` output.

## Run fork CI

The workflow runs only in `chinrw/asterinas` on
`experiment/crate2nix-ci-cache`.
It reuses the owner's public `asterina-test` cache and the existing
`CACHIX_AUTH_TOKEN_FOR_TEST_CACHE` secret.
Only the seed publication step receives this credential.
Consumers receive public read access and never publish their modified outputs.
The seed explicitly uploads all crate outputs and the target sysroot,
then verifies their remote metadata before consumers start.

A branch push defaults to a single probe trial.
Set the fork's `CRATE2NIX_MODE` variable to `kernel` and
`CRATE2NIX_REPETITIONS` to `1` or `5` before a subsequent branch push
to select complete-kernel trials.
This route does not require changes to the fork's default branch.
Manual dispatch offers the same choices when GitHub makes the workflow dispatchable.

Each consumer gets a fresh runner.
Kernel trials also run independent Cargo and Nix cold comparisons.
Kernel realization timings start after preparing the common pinned environment.
Successful Cargo and Nix consumer samples both run the boot smoke and the existing
hello-world tests; changed samples boot their actual newly produced ELF.
Artifacts contain graphs, raw logs, metrics, and job metadata;
they do not transfer compiled outputs between jobs.
Five repetitions are intended for a performance decision;
the default single trial establishes correctness only.
The report rejects timed-out or failed jobs even when they uploaded an earlier
successful compilation metric.

## Adaptations and maintenance

`resolver2.patch` separates host and target feature resolution in the generated
kernel evaluator.
`feature-names.json` preserves legal empty features while removing dependency
selection markers from the flags passed to rustc.
The builder obtains build-script cfg values from the real target compiler.
The shared compiler flags and build-std arguments come from OSDK's exported command
in `build-contract.json`.
Packages without an actual custom-build target do not acquire build dependencies
merely because they declare an unused build-dependencies section.

The component macro normally invokes Cargo and reads `Components.toml`.
The adapter supplies the package names, component dependency edges, and paths
that this macro consumes, projected from real workspace metadata.
Stable per-component source mappings preserve its `file!()` registry matching
without putting the entire checkout into every crate's source.
The vDSO is the existing pinned development-environment input.

The pinned smoltcp build script emits configuration declarations in HashMap order.
Its override sorts the generated declarations without changing their values;
the original unsorted output failed a strict rebuild comparison.
The sysroot is built with the original Cargo build-std flags in a separate derivation.
Its lock is captured during generation so evaluation can disable IFD.
Compiler source paths are remapped to avoid pulling the whole toolchain
into the kernel's runtime closure.

The OSDK additions export its actual build command and consume a prebuilt ELF.
Normal builds still use the same command constructor.
Experiments can fix the build username and hostname through the corresponding
`OSDK_BUILD_*` variables, with the previous identity lookup as the default.
The Cargo comparison and Nix adapter both use `crate2nix-ci` for those fields.
