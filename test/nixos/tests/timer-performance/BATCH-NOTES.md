# Temporary timer batch diagnosis

This fork-only campaign compares frozen base e0d8b839f95f608c8a7e9c8942e01614a3b3b260 and retirement 64848d3e0a23ff0903f4be485fb7750c1709a575 on four virtual CPUs. It keeps the original five-second application windows and forward workload history. The first fresh pair has the recorder disabled; three further fresh pairs enable it only around fixed-rate network windows. Every guest runs the same helper daemon. These instrumented builds are diagnostic evidence, not production performance acceptance.

The build first verifies plain kernel source identity, then injects the observer in its disposable checkout. It preserves the generated sources, patch, dependency locks and each ELF, then restores the tracked kernel files. The production repair remains unchanged. The helper records timer batch admission, queue acquisition, collection, callback completion, queue sizes and outcome counts. The threshold is five milliseconds, including collection-time lateness of invoked callbacks. Frozen buffers are exported after all application workloads finish.

QMP is used only to map guest CPUs to host threads. This campaign never pauses the VM. Pausing the earlier QMP campaign changed TCP recovery timing, so those post-pause maxima cannot be treated as natural workload latency.

Stage durations are elapsed TSC time, including interrupts, migration and host descheduling. They are not CPU time. Recording and shared aggregate publication after the final timestamp add overhead outside the reported stages. The disabled pair exposes some of this effect, but one pair is not a precise overhead estimate. A recorded maximum describes observed calls only; it is not a service-latency guarantee.

Host/guest clock samples bracket both ends of each window. Their constant-offset intervals may be disjoint because the nominal guest TSC frequency and host monotonic clock differ. Metadata explicitly records this. Interpolation assumes an affine relation over the window and must retain endpoint uncertainty; collection completeness alone does not prove that assumption.

Validation rejects missing phases, insufficient load, incomplete packet streams, malformed epochs, buffer overflow and nonmonotonic TSC stages. Failed samples and unattempted samples remain in the denominator. Empty trace output with the recorder disabled is expected. Calibration runs that deliberately overflow the buffer are kept separately and are never performance evidence.
