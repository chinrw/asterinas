# SPDX-License-Identifier: MPL-2.0

{
  stdenv,
  fetchurl,
  bc,
  bison,
  elfutils,
  flex,
  pkg-config,
  python3,
  kernel,
  config,
}:
stdenv.mkDerivation {
  pname = "benchmark-linux-regression-modules";
  version = "6.16";
  src = fetchurl {
    url = "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.16.tar.xz";
    sha256 = "1a4be2fe6b5246aa4ac8987a8a4af34c42a8dd7d08b46ab48516bcc1befbcd83";
  };

  nativeBuildInputs = [
    bc
    bison
    flex
    pkg-config
    (python3.withPackages (ps: [ ps.pyelftools ]))
  ];
  buildInputs = [ elfutils ];
  hardeningDisable = [ "all" ];
  dontPatchELF = true;
  dontConfigure = true;

  postPatch = ''
    patchShebangs scripts arch/x86/tools tools/objtool
  '';

  buildPhase = ''
    runHook preBuild
    cp ${config} .config
    grep -Fxq '# CONFIG_MODVERSIONS is not set' .config
    make olddefconfig
    make -j"$NIX_BUILD_CORES" modules_prepare

    scripts/extract-vmlinux ${kernel} > "$TMPDIR/benchmark.elf"
    python ${./kernel-build-data.py} "$TMPDIR/benchmark.elf"
    tar -xf benchmark-headers.tar

    mkdir -p "$TMPDIR/modules"
    cp -r fs/overlayfs fs/exfat net/vmw_vsock "$TMPDIR/modules/"
    pushd "$TMPDIR/modules"
    echo 'obj-m += overlayfs/ exfat/ vmw_vsock/' > Kbuild
    # exFAT is an external module for this image. Use the upstream default charset.
    sed 's/obj-$(CONFIG_EXFAT_FS)/obj-m/' exfat/Makefile > exfat/Kbuild
    cat >> exfat/Kbuild <<'EOF'
    ccflags-y += -DCONFIG_EXFAT_DEFAULT_IOCHARSET='"utf8"'
    EOF
    # The socket ioctl workload only needs the core AF_VSOCK module.
    grep '^vsock-' vmw_vsock/Makefile > vmw_vsock/Kbuild
    echo 'obj-m += vsock.o' >> vmw_vsock/Kbuild
    popd
    make -j"$NIX_BUILD_CORES" M="$TMPDIR/modules" modules
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    release=$(sed -n 's/^#define UTS_RELEASE "\(.*\)"/\1/p' include/generated/utsrelease.h)
    test -n "$release"
    install -Dm644 -t "$out/lib/modules/$release" \
      "$TMPDIR/modules/overlayfs/overlay.ko" \
      "$TMPDIR/modules/exfat/exfat.ko" \
      "$TMPDIR/modules/vmw_vsock/vsock.ko"
    runHook postInstall
  '';
}
