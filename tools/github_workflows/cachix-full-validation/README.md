# Replay Cachix validation artifacts

Use the analyzer to compare the saved producer and consumer artifacts
without rebuilding packages or uploading caches.
It requires Python 3 and the extracted artifacts from the native host matrix.

Keep each artifact in its own directory under an artifact root:
`published-{x86_64,aarch64}` and
`consumers-{x86_64,aarch64}-{timing,profile}`.
Run from the repository root and choose a new output file outside the artifact root:

```bash
python3 tools/github_workflows/cachix-full-validation/analyze.py \
    /path/to/artifacts --output /path/to/comparison.json
```

The analyzer leaves input artifacts unchanged and refuses to overwrite its output.
A successful analyzer process means that it wrote the report;
check `validation_passed`, `incomplete`, and `validation_failures` for the experiment result.
A missing bootstrap record leaves `bootstrap_s` unknown and the job incomplete.
Consumer timings remain available when present,
but composite timings require a successful bootstrap measurement.

## Closure equivalence

The benchmark and analyzer compare the full runtime closures of the selected Make outputs.
They require the same paths, every NAR hash, and every reference set.
Empty closures, missing required roots, absent metadata, and references outside the closure fail the comparison.
Root-only hashes remain diagnostic fields and cannot establish closure equivalence.
The analyzer preserves the recorded workflow result in `complete`;
`validation_passed` additionally requires the replayed checks to pass.
Differences in independent source builds remain failures even when cached consumers match the publisher.

## Disk observations

`disk_sampling.max_observed_nix_tmp_bytes` is the largest valid observed
`du -sk /nix /tmp` traversal total, converted to bytes.
The traversal is not atomic while files change,
so this value is neither an exact peak nor a guaranteed lower bound on a peak.
Host filesystem observations use `max_observed_used_bytes` separately;
they include retained build images and other host data.

Each new scan records its start, end, duration, exit code, output, diagnostics, and validity.
Partial or failed scans are retained but excluded from the maximum.
A failed scan or sampler invalidates the profile and makes the collector exit nonzero,
even when the measured command succeeds.
Required pre-GC snapshots also fail on scan errors.
The command's own exit code remains in the measurement record.

Historical samples without scan validity remain `unknown`.
Their complete totals appear only in `legacy_max_observed_nix_tmp_bytes`.
No valid samples means the validated maximum is `null`, never zero.
The timing runs do not scan the store.
These changes preserve the existing GC commands and their order.
