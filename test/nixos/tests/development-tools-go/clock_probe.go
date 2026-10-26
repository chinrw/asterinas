// SPDX-License-Identifier: MPL-2.0

package main

import (
	"fmt"
	"syscall"
	"time"
	"unsafe"
)

func rawClock(clock uintptr) int64 {
	var value syscall.Timespec
	_, _, errno := syscall.RawSyscall(syscall.SYS_CLOCK_GETTIME, clock, uintptr(unsafe.Pointer(&value)), 0)
	if errno != 0 {
		panic(errno)
	}
	return value.Sec*1e9 + value.Nsec
}

func main() {
	kernelStart := rawClock(1)
	realtimeStart := rawClock(0)
	goStart := time.Now()
	request := syscall.Timespec{Sec: 1}
	for {
		var remaining syscall.Timespec
		err := syscall.Nanosleep(&request, &remaining)
		if err == syscall.EINTR {
			request = remaining
			continue
		}
		if err != nil {
			panic(err)
		}
		break
	}
	goEnd := time.Now()
	fmt.Printf("CLOCK_PROBE go_monotonic_ns=%d kernel_monotonic_ns=%d go_realtime_ns=%d kernel_realtime_ns=%d\n",
		goEnd.Sub(goStart).Nanoseconds(), rawClock(1)-kernelStart,
		goEnd.UnixNano()-goStart.UnixNano(), rawClock(0)-realtimeStart)
}
