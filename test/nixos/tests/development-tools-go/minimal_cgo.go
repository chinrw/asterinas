// Copyright 2025 The Go Authors. All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the GO-LICENSE file.

package main

/*
#include <stdatomic.h>
#include <pthread.h>
extern void BlockForeverInGo();
static _Atomic int spinning;
static void* enterGoThenWait(void* ignored) {
    (void)ignored;
    BlockForeverInGo();
    return NULL;
}
static int WaitInGoInNewCThread() {
    pthread_t thread;
    return pthread_create(&thread, NULL, enterGoThenWait, NULL);
}
static void SpinForever() {
    atomic_fetch_add(&spinning, 1);
    for (;;) {}
}
static int Spinning() { return atomic_load(&spinning); }
*/
import "C"

import (
    "os"
    "runtime"
    "sync/atomic"
)

var blockedForever atomic.Uint32

//export BlockForeverInGo
func BlockForeverInGo() {
    blockedForever.Add(1)
    select {}
}

func main() {
    for range 10 {
        if result := C.WaitInGoInNewCThread(); result != 0 {
            println("PTHREAD_CREATE_FAILED", result)
            os.Exit(2)
        }
    }
    for range 10 {
        go func() { C.SpinForever() }()
    }
    for C.Spinning() < 10 || blockedForever.Load() < 10 {
        if len(os.Args) > 1 && os.Args[1] == "yield" {
            runtime.Gosched()
        }
    }
    println("BARRIER_OK")
}
