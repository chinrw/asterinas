# Timer lifecycle results on current main

Compare acf7dcf5e with f48cf611f. Sixteen local Ryzen 9 9950X QEMU/KVM guests use
one or four CPUs and 8GiB. Each CPU setting has four pairs, two in each order.
Every value and slow trial is retained. Positive cost changes mean more time;
positive throughput changes mean more requests per second. Percent changes are
medians of within-pair ratios, not ratios of the displayed independent medians.

## Timerfd

### Average operation costs, ns/op

Targets are timerfds created by one benchmark process, not the system timer
population or a per-CPU count. The helper has one foreground thread. Values are
medians of four per-run stage averages, not per-call tail latencies.

| Scene               | Operation   | Base  | Head  | Paired change |
| ------------------- | ----------- | ----- | ----- | ------------- |
| 1 CPU, 64 targets   | arm         | 250.0 | 360.7 | +44.3%        |
| 1 CPU, 64 targets   | cancel      | 140.2 | 212.0 | +51.6%        |
| 1 CPU, 64 targets   | rearm       | 382.5 | 422.9 | +10.9%        |
| 1 CPU, 64 targets   | armed close | 226.6 | 361.6 | +60.9%        |
| 1 CPU, 4096 targets | arm         | 168.5 | 421.5 | +152.9%       |
| 1 CPU, 4096 targets | cancel      | 142.8 | 279.5 | +94.4%        |
| 1 CPU, 4096 targets | rearm       | 184.8 | 477.8 | +159.0%       |
| 1 CPU, 4096 targets | armed close | 260.7 | 409.5 | +50.3%        |
| 4 CPU, 64 targets   | arm         | 289.6 | 605.2 | +122.0%       |
| 4 CPU, 64 targets   | cancel      | 140.8 | 211.1 | +49.9%        |
| 4 CPU, 64 targets   | rearm       | 291.5 | 412.9 | +46.5%        |
| 4 CPU, 64 targets   | armed close | 235.8 | 340.5 | +40.7%        |
| 4 CPU, 4096 targets | arm         | 277.0 | 314.7 | +19.6%        |
| 4 CPU, 4096 targets | cancel      | 147.8 | 219.8 | +48.7%        |
| 4 CPU, 4096 targets | rearm       | 214.3 | 398.4 | +89.9%        |
| 4 CPU, 4096 targets | armed close | 271.7 | 385.8 | +41.8%        |

### Complete expiry read lateness, ms

Expiry spans deadline to completed read and includes delivery, scheduling,
polling, scanning and reads. The p99 is a median of run quantiles; worst max
retains the worst individual observation. At 64 targets, per-run p99 equals max.

| Scene               | Metric    | Base  | Head  |
| ------------------- | --------- | ----- | ----- |
| 1 CPU, 64 targets   | p99       | 0.773 | 0.701 |
| 1 CPU, 64 targets   | worst max | 0.916 | 1.020 |
| 1 CPU, 4096 targets | p99       | 1.876 | 2.402 |
| 1 CPU, 4096 targets | worst max | 2.966 | 3.242 |
| 4 CPU, 64 targets   | p99       | 0.690 | 0.599 |
| 4 CPU, 64 targets   | worst max | 0.983 | 0.998 |
| 4 CPU, 4096 targets | p99       | 2.032 | 2.011 |
| 4 CPU, 4096 targets | worst max | 4.973 | 2.342 |

## Redis

### GET capacity, thousand requests/s

Capacity uses 100000 closed-loop GETs and twenty connections. The separate
fixed-arrival experiment offers 1000 GET/s for five seconds on twenty connections.
Churn requests 5000 cycles/s on 128 timerfds and must reach at least 4500 cycles/s.
Throughput does not establish tail latency.

| Scene        | Base  | Head  | Paired change |
| ------------ | ----- | ----- | ------------- |
| 1 CPU, none  | 174.7 | 197.8 | +16.2%        |
| 1 CPU, churn | 182.0 | 206.0 | +14.1%        |
| 4 CPU, none  | 183.6 | 184.8 | +5.6%         |
| 4 CPU, churn | 196.1 | 189.1 | -1.8%         |

### Fixed-arrival latency, ms

Service spans actual request start to response completion, including network and
host processing. Client queue spans scheduled arrival to actual start. Total spans
scheduled arrival to response completion. Quantiles are medians of four per-run
p99.9 values, not pooled percentiles; maxima are the worst observed requests.

| Scene        | Interval     | Base p99.9 | Head p99.9 | Base max | Head max |
| ------------ | ------------ | ---------- | ---------- | -------- | -------- |
| 1 CPU, none  | service      | 5.399      | 13.015     | 31.306   | 23.754   |
| 1 CPU, none  | client_queue | 1.071      | 1.072      | 11.551   | 4.189    |
| 1 CPU, none  | total        | 6.349      | 13.459     | 31.750   | 24.369   |
| 1 CPU, churn | service      | 0.188      | 0.232      | 0.517    | 10.625   |
| 1 CPU, churn | client_queue | 1.068      | 1.069      | 1.086    | 4.510    |
| 1 CPU, churn | total        | 1.193      | 1.202      | 1.270    | 10.788   |
| 4 CPU, none  | service      | 1.374      | 6.393      | 5.666    | 16.107   |
| 4 CPU, none  | client_queue | 1.076      | 1.074      | 1.091    | 1.095    |
| 4 CPU, none  | total        | 1.805      | 6.661      | 5.799    | 17.068   |
| 4 CPU, churn | service      | 0.190      | 0.208      | 3.076    | 6.308    |
| 4 CPU, churn | client_queue | 1.074      | 1.076      | 1.090    | 3.295    |
| 4 CPU, churn | total        | 1.218      | 1.216      | 3.713    | 7.269    |

### Successful intervals strictly above 10ms

There are 20000 GETs per scene and variant, with zero errors and no missing IDs.
A request can count in multiple intervals, so these columns must not be summed.

| Scene        | Interval     | Base count | Head count |
| ------------ | ------------ | ---------- | ---------- |
| 1 CPU, none  | service      | 22         | 31         |
| 1 CPU, none  | client_queue | 2          | 0          |
| 1 CPU, none  | total        | 25         | 33         |
| 1 CPU, churn | service      | 0          | 1          |
| 1 CPU, churn | client_queue | 0          | 0          |
| 1 CPU, churn | total        | 0          | 1          |
| 4 CPU, none  | service      | 0          | 14         |
| 4 CPU, none  | client_queue | 0          | 0          |
| 4 CPU, none  | total        | 0          | 15         |
| 4 CPU, churn | service      | 0          | 0          |
| 4 CPU, churn | client_queue | 0          | 0          |
| 4 CPU, churn | total        | 0          | 0          |

## Full ranges across the four runs

These are ranges of run-level metrics, not request-level minima and maxima.

### Timerfd expiry read p99, ms

| Scene               | Base range     | Head range     |
| ------------------- | -------------- | -------------- |
| 1 CPU, 64 targets   | 0.529 to 0.916 | 0.106 to 1.020 |
| 1 CPU, 4096 targets | 1.580 to 2.957 | 1.945 to 3.231 |
| 4 CPU, 64 targets   | 0.231 to 0.983 | 0.299 to 0.998 |
| 4 CPU, 4096 targets | 1.420 to 4.966 | 1.813 to 2.336 |

### Redis capacity, thousand requests/s

| Scene        | Base range     | Head range     |
| ------------ | -------------- | -------------- |
| 1 CPU, none  | 146.6 to 194.9 | 166.1 to 208.8 |
| 1 CPU, churn | 179.9 to 204.5 | 194.6 to 215.5 |
| 4 CPU, none  | 156.5 to 201.6 | 163.1 to 202.0 |
| 4 CPU, churn | 187.3 to 200.8 | 179.5 to 196.9 |

### Redis p99.9, ms

| Scene        | Interval     | Base range      | Head range      |
| ------------ | ------------ | --------------- | --------------- |
| 1 CPU, none  | service      | 3.781 to 25.921 | 2.879 to 18.411 |
| 1 CPU, none  | client_queue | 1.068 to 6.666  | 1.069 to 1.087  |
| 1 CPU, none  | total        | 3.916 to 26.741 | 3.142 to 19.345 |
| 1 CPU, churn | service      | 0.177 to 0.200  | 0.176 to 5.121  |
| 1 CPU, churn | client_queue | 1.062 to 1.073  | 1.059 to 3.474  |
| 1 CPU, churn | total        | 1.173 to 1.199  | 1.161 to 5.963  |
| 4 CPU, none  | service      | 0.817 to 2.076  | 0.190 to 11.892 |
| 4 CPU, none  | client_queue | 1.070 to 1.080  | 1.065 to 1.081  |
| 4 CPU, none  | total        | 1.320 to 2.622  | 1.221 to 12.130 |
| 4 CPU, churn | service      | 0.175 to 0.224  | 0.186 to 3.878  |
| 4 CPU, churn | client_queue | 1.072 to 1.075  | 1.074 to 1.356  |
| 4 CPU, churn | total        | 1.198 to 1.232  | 1.193 to 5.251  |
