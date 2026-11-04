# Timer queue census

这套临时诊断在同一生命周期修复参考上测量 timer manager 的操作频率、规模和 elapsed TSC 分布。运行者需要已冻结的内核、userland 镜像、helper 哈希及独占的 QEMU 身份记录。先在一个 fresh guest 预检，再由外层 runner 安排 CPU=1/4、独立 guest 和配对顺序。这里的脚本不启动或停止虚拟机；Go 的宿主 watchdog 会在原有 3 秒截止超时后，通过 `owned_qemu.py` 停止其身份记录所指的 QEMU。

## 构建和镜像

在项目 Docker 容器内、仓库根目录构建 helper。表达式使用仓库锁定的 nixpkgs，不额外引入运行时库。

```sh
nix-build --no-out-link --expr '
  let pkgs = import ./distro/nixpkgs.nix {};
  in import ./test/nixos/tests/timer-performance/census/helper.nix { inherit pkgs; }
'
```

输出的 `bin/timer-census` 和 `bin/census-workload` 都只依赖 C 运行时。镜像复用前，外层 runner 仍应将 `nix-store -qR` 和 ELF interpreter 与旧 helper 的实际 closure 比较；源码相似不能证明旧镜像已经包含新输出所需的 interpreter。

完整 NixOS 镜像由 `extra_config.nix` 安装以下文件：

| 默认路径 | 内容 |
|---|---|
| `/etc/timer-perf/timer-census` | UDP census daemon。 |
| `/etc/timer-perf/census-workload` | idle、future 和 periodic 对照负载。 |
| `/etc/timer-perf/census-export.py` | 冻结快照的压缩 console 导出。 |

复用旧 root 镜像时可注入 `/opt/timer-census/{timer-census,census-workload,export.py}`，并将三个路径环境变量指向相应文件。保留原镜像的 `/etc/timer-perf/{timer-perf,setup.py,loadcheck.py}` 和 `/etc/notingo/minimal`。内核 ABI 定义以同目录 `abi.json` 为准。

host 测试驱动仍从 `test/nixos/Cargo.toml` 构建，使用现有 `host.lock`。复制锁文件、构建或注入只在外层 runner 管理的可丢弃 fixture 中进行。

## 单个 guest 的入口

外层 runner 沿用 `paired.py` 的镜像哈希、QEMU owner、宿主 PID/start ticks、独立 sample 目录、TAP 清理和 watchdog 约束，执行：

```sh
make run_nixos NIXOS_TEST_SUITE=timer-performance \
  NIXOS_TEST_CASE=queue_census NIXOS_TEST_TIMEOUT=900s
```

保留以下既有环境变量：`SMP`、`PERF_SAMPLE_DIR`、`PERF_HELPER_SHA256`、`PERF_HOST_CLIENTS`、`PERF_BLOCK`、`PERF_CLIENT_HOST`、`NGINX_PORT`、`REDIS_PORT`、`PROBE_QEMU_OWNER`、`PROBE_SAMPLE_ID`、`PROBE_QEMU_CONTROL`、`PROBE_HOST_MARKER` 和 `PROBE_HELPER_SHA256`。`PROBE_HOST_SAMPLE` 必须为 `minimal`。`PROBE_HOST_MARKER` 放在当前 fresh sample 目录内；每次 Go 结果会先复制到独立文件，再复用这个既有接口。关闭其他诊断，设置 `TAIL_BATCH=0`、`TAIL_PERF=0` 和 `TAIL_QMP=0`。

诊断 guest 需要能从宿主访问 `10.0.2.15:9997/UDP`。现有 TAP 路线可直接使用；若配置转发，应设置 `PERF_CLIENT_HOST` 和 `CENSUS_PORT`，并同时保持 Redis/nginx 的真实路由。

| 环境变量 | 含义 |
|---|---|
| `CENSUS_HELPER_SHA256` | daemon 二进制的 SHA-256。诊断模式必填。 |
| `CENSUS_WORKLOAD_SHA256` | 补充负载二进制的 SHA-256。所有模式必填。 |
| `CENSUS_HELPER_PATH` | 默认 `/etc/timer-perf/timer-census`，或 `/opt/timer-census/timer-census`。 |
| `CENSUS_WORKLOAD_PATH` | 默认 `/etc/timer-perf/census-workload`，或 `/opt/timer-census/census-workload`。 |
| `CENSUS_EXPORT_PATH` | 默认 `/etc/timer-perf/census-export.py`，或 `/opt/timer-census/export.py`。 |
| `CENSUS_CONTROL_SCRIPT` | 宿主 `census/control.py` 的绝对路径。 |
| `CENSUS_REDIS_SCRIPT` | 宿主 `census/redis_client.py` 的绝对路径。 |
| `CENSUS_MODES` | 默认 `0,1,2`，也可只选一个。每个负载的 mode 顺序按固定 seed 洗牌。 |
| `CENSUS_ORDER_SEED` | 默认 `20261104`，实际顺序保存到 `census-plan.json`。 |
| `CENSUS_GO_RUNS` | 每个 Go 窗口的独立进程次数，默认 5，范围 1..100。每次保留原 3 秒绝对宿主截止。 |
| `CENSUS_CASES` | 逗号分隔的负载名，用于明确筛选。 |
| `CENSUS_PREFLIGHT=1` | 将默认负载缩为 idle/future/Go、三个代表微测试、periodic 和两种 Redis。显式 `CENSUS_CASES` 优先。 |
| `CENSUS_PLAIN=1` | 跳过 daemon、所有 prctl 控制及快照导出，运行同一负载和宿主性能记录。 |

真正 pristine 参考必须由不含 census hooks/字段的内核配合 `CENSUS_PLAIN=1` 运行。诊断内核的 mode 0 只关闭运行时记录，仍包含静态 hooks 和类型布局；mode 0/1/2 的比较只能解释该诊断构建内部的观察开销。配对 pristine 时使用相同负载、次数和顺序；外层 runner 决定配对块和 fresh guest 数。

## 负载和窗口

完整负载名为 `idle`、`future`、`go`、`micro_{1,64,4096}_{cancel,drop,rearm,expiry}`、`periodic`、`redis` 和 `redis_churn`。idle 睡眠 2 秒，future 同时保留一个一小时后的 timerfd；二者都包含控制进程自己的睡眠 timer。periodic 使用 64 个 20ms 周期 timerfd，持续 2 秒，并要求每个 timer 至少被读取一次有效到期。微测试复用现有 sentinel、过期读取和 drain 验收。

Redis 复用 `host_clients.fixed_rate`，固定 20 个连接、1000 请求/秒、5 秒，共 5000 个 GET，保留全部调度、完成及错误记录。窗口还包含连接建立和 fixture 检查。`redis_churn` 在窗口之前启动现有 5000 cycles/s 生产者，窗口之后停止，并调用原 `loadcheck.py` 验证 PID、完整覆盖、cycles/skipped、清理和声明速率的 90% 门槛。每个窗口的生产者日志及 begin/end 时间戳都使用独立 case 名。

Rust 在宿主端调用 START，运行 phase 或 Redis client，验证完成后调用 FREEZE。START 和 FREEZE 的宿主控制耗时不计入 `census-windows.jsonl` 的负载计时；快照 gate 本身仍覆盖 shell 分发、console 输出、控制收包边界和 guest 调度。这不是仅测 workload 函数体的窗口。所有 helper 文件写入都在 STOP 成功且 ACTIVE 为零之后，所有 console 快照导出都在最后一个负载窗口之后。

daemon 只有一个 OWNER PID。请求是 6 个 little-endian u64，回复是 14 个 u64，宿主严格核对 magic/version/command/nonce/case/mode、errno、TSC、frequency、epoch、bytes、重试次数和 owner。STOP 的 EBUSY 最多在 2 秒内重试 2000 次；宿主 socket 的 START/PING/CLOCK 超时为 3 秒，FREEZE 为 6 秒。START UDP 超时不会重发，因为其效果未知。case ID 单调递增，已有 raw/JSON 文件拒绝覆盖。内核要求初始 user namespace 的 `CAP_SYS_ADMIN`，daemon 以 root 运行。 若控制 daemon 退出或协议失败，本 guest 必须判失败，并由 owned-QEMU cleanup 清理后启动 fresh guest；当前严格 OWNER 契约不允许更换 PID 续接活跃 case。

## 恢复和验收

每个冻结窗口在 guest 的 `/var/tmp/timer-census-ID.bin` 保存完整 327808 字节快照，另有只包含非零 CPU/kind 行的 JSON。`timer-census --dump FILE` 可离线将原始文件转换为同样的紧凑 JSON。结束时 `export.py` 为每个原始快照输出 gzip/base64 分块、原始及压缩 SHA-256、长度和严格边界。

保留每个 VM 的磁盘；宿主同时从原始 QEMU serial 日志恢复快照：

```sh
python3 test/nixos/tests/timer-performance/census/decode.py \
  /absolute/sample/qemu.log /absolute/sample/decoded \
  --sample-dir /absolute/sample --cpus 1
python3 -B test/nixos/tests/timer-performance/census/test_decode.py
```

`decoded` 必须尚不存在。解码器拒绝重复或缺失 chunk、坏哈希、尺寸/header/epoch/mode 不一致、非 quiescent、越界 CPU 行、mode 0 非零计数和 mode 1 非零 timing。带 `--sample-dir` 时，还必须完整覆盖计划中的全部窗口，且 START/FREEZE 记录完整。每行核对 dispatch、minimum probes、callback outcomes、retirement intents、periodic 子集及 histogram 恒等式。若出现 `tsc_backwards>0`，先保存恢复的 raw/JSON，再以非零退出报告 timing 质量失败。

`census-windows.jsonl` 保存每个负载的宿主 elapsed 和 QEMU CPU ticks；`phases.jsonl` 保留既有 phase 验收，`census-go-ID-REP.json` 保留每次 Go 截止和 QEMU 身份，`census-redis-ID-fixed.json` 保存全部 Redis 请求。样本验收还需要外层 runner 确认 `complete`、所有 phase、producer 和 owned-QEMU cleanup 都成功。解码成功本身不构成应用表现或虚拟机清理的验收。

CPU/kind 行表示执行 CPU 和 manager 类别，并非单个 manager 身份。occupancy 是逐次队列观察，不能将 CPU-time 多个 manager 的 occupancy 相加解释成一个队列规模。现有 ABI 不含每个 manager 的 `N_start/N_end`，因此只能检查事件恒等式，不能宣称已验证跨窗口队列余额。elapsed TSC 包含宿主停调度和嵌套范围，不是 CPU time，也不能独立说明应用尾延迟。
