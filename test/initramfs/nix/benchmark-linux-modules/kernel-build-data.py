# SPDX-License-Identifier: MPL-2.0

"""Recover kheaders and Module.symvers from the benchmark's stripped x86-64 ELF."""

import io
import lzma
from pathlib import Path
import re
import struct
import sys
import tarfile

from elftools.elf.elffile import ELFFile


def extract_headers(elf):
    # CONFIG_IKHEADERS embeds kheaders.tar.xz in .rodata. The stripped image
    # does not retain the kernel_headers_data symbol that locates it at runtime.
    rodata = elf.get_section_by_name(".rodata").data()
    for match in re.finditer(b"\xfd7zXZ\x00", rodata):
        try:
            archive = lzma.LZMADecompressor().decompress(rodata[match.start() :])
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as headers:
                headers.getmember("./include/generated/utsrelease.h")
        except (lzma.LZMAError, tarfile.ReadError, KeyError):
            continue
        Path("benchmark-headers.tar").write_bytes(archive)
        return
    raise ValueError("Missing embedded kheaders in the benchmark kernel")


def write_exports(elf):
    strings = elf.get_section_by_name("__ksymtab_strings")
    names = strings.data()

    def string_at(address):
        offset = address - strings["sh_addr"]
        if not 0 <= offset < len(names):
            raise ValueError("Invalid exported symbol string offset")
        return names[offset : names.index(b"\0", offset)].decode("ascii")

    # This x86-64 image uses three field-relative 32-bit offsets per export.
    # CONFIG_MODVERSIONS is disabled, so modpost needs no symbol CRCs.
    with Path("Module.symvers").open("w") as output:
        for section, kind in [
            ("__ksymtab", "EXPORT_SYMBOL"),
            ("__ksymtab_gpl", "EXPORT_SYMBOL_GPL"),
        ]:
            exports = elf.get_section_by_name(section)
            address = exports["sh_addr"]
            for _, name, namespace in struct.iter_unpack("<iii", exports.data()):
                symbol = string_at(address + 4 + name)
                ns = string_at(address + 8 + namespace)
                output.write(f"0x00000000\t{symbol}\tvmlinux\t{kind}\t{ns}\n")
                address += 12


with open(sys.argv[1], "rb") as image:
    kernel = ELFFile(image)
    extract_headers(kernel)
    write_exports(kernel)
