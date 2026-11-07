#!/usr/bin/env python3
# SPDX-License-Identifier: MIT  (see LICENSE)
"""RS7000 dump post-processing.

  mem-log   LOG...      rebuild the CS0 memory image from raw console transcripts
                         (address-driven, so retries/duplicates/gaps are handled)
  wave-log  LOG...      rebuild the wave word image from raw transcripts
  carve     FLASH.bin   split the CS0 image into the 2 MB chips it contains (full 6 MB dump,
                         or a partial one such as cs0_flash_00400000.bin = IC93 only)
  split-wave WAVE.bin   split the 32-bit word image into the two 16-bit-wide ROMs
  check-update FLASH.bin 0532OS_.PGM
                         compare the dump with the flash contents implied by Yamaha's updater
  analyze-wave WAVE.bin  heuristic: which half/byte order gives smooth 16-bit audio?

Every command takes --target rs7000|es|motif (default rs7000).  carve writes the chip files and a merged
program file (rs7000: ic37 / ic79 / ic93 + rs7000_program_merged.bin; motif: ic31 / ic25 de-interleaved +
motif_program_merged.bin, or ic101_mask.bin from a mask ROM dump; es: es_program.bin only, chip layout unknown).
For es and motif: check-update
understands the `MOTIF2 OS File` (ES) and `WSSx OS File` (original Motif, 0638OS_.PGM) updaters, and
the wave defaults are 96 MB / hi_lo (es) and lo_hi with no size default (motif).

The line parsers are the ones the dumper uses (the ASCII column of every memory
line is cross-checked against its hex bytes).
"""
import argparse, hashlib, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util
def _load_cns():
    """rs7k-motif_cns.py: the shared console code (its name has a hyphen, so it is loaded by path, not imported)."""
    name = "rs7k_motif_cns"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(os.path.abspath(__file__)), "rs7k-motif_cns.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
C = _load_cns()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def cmd_mem_log(a):
    size = a.size
    img = bytearray(b"\xff" * size)
    have = bytearray(size // 16)
    conflicts = 0
    for path in a.logs:
        with open(path, "r", encoding="latin-1") as f:
            text = f.read()
        for addr, data in C.parse_mem_lines(text):
            off = (addr & 0x1FFFFFFF) - a.start
            if not 0 <= off < size:
                continue
            if have[off // 16] and img[off:off + 16] != data:
                conflicts += 1
            img[off:off + 16] = data
            have[off // 16] = 1
    missing = [i for i, h in enumerate(have) if not h]
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "wb") as f:
        f.write(img)
    print("lines placed: %d / %d   conflicts between duplicate lines: %d"
          % (len(have) - len(missing), len(have), conflicts))
    if missing:
        runs = []
        s = p = missing[0]
        for i in missing[1:]:
            if i != p + 1:
                runs.append((s, p)); s = i
            p = i
        runs.append((s, p))
        print("MISSING %d lines in %d range(s); first ones:" % (len(missing), len(runs)))
        for s, p in runs[:10]:
            print("  0x%08X - 0x%08X" % (a.start + s * 16, a.start + p * 16 + 15))
    print("wrote", a.out, "sha256", sha(bytes(img)))
    return 0 if not missing and not conflicts else 1


def cmd_wave_log(a):
    nwords = a.words
    img = bytearray(nwords * 4)
    have = bytearray(nwords // 4)
    conflicts = 0
    for path in a.logs:
        with open(path, "r", encoding="latin-1") as f:
            text = f.read()
        for waddr, words in C.parse_wave_lines(text, a.half_order):
            w = waddr - a.first_word
            if not 0 <= w < nwords:
                continue
            b = b"".join(x.to_bytes(4, "big") for x in words)
            if have[w // 4] and img[w * 4:w * 4 + 16] != b:
                conflicts += 1
            img[w * 4:w * 4 + 16] = b
            have[w // 4] = 1
    missing = [i for i, h in enumerate(have) if not h]
    with open(a.out, "wb") as f:
        f.write(img)
    print("lines placed: %d / %d  conflicts: %d" % (len(have) - len(missing), len(have), conflicts))
    if missing:
        print("MISSING %d lines; first word addresses:" % len(missing),
              [hex(a.first_word + i * 4) for i in missing[:10]])
    print("wrote", a.out, "sha256", sha(bytes(img)))
    return 0 if not missing and not conflicts else 1


def style_signature(chunk):
    """The OS self-test (ROM 0xCE8FA) accepts the style ROM when the 16-bit sum of the
    big-endian words at 0x400000 + 2^k (k = 1..20) and at 0x5FFFFE equals 0xAA55."""
    offs = [1 << k for k in range(1, 21)] + [0x1FFFFE]
    return sum(int.from_bytes(chunk[o:o + 2], "big") for o in offs) & 0xFFFF


def image_start(a):
    """CPU-physical address of byte 0 of the file: --start, else taken from a
    cs0_flash_XXXXXXXX.bin file name, else 0."""
    if a.start is not None:
        return a.start
    m = re.search(r"cs0_flash_([0-9A-Fa-f]{8})\.bin", os.path.basename(a.flash))
    return int(m.group(1), 16) if m else 0


def cmd_carve(a):
    d = open(a.flash, "rb").read()
    base = image_start(a)
    print("image starts at CPU address 0x%06X, %d bytes" % (base, len(d)))
    os.makedirs(a.out, exist_ok=True)
    if a.target == "motif":
        return carve_motif(a, d, base)
    if a.target == "es":
        # ES program flash: chip split unknown, keep it whole.  Sanity: version string at 0x20000.
        ver = d[0x20000 - base:0x20008 - base] if base <= 0x20000 else b""
        with open(os.path.join(a.out, "es_program.bin"), "wb") as f:
            f.write(d)
        print("es_program.bin      sha256 %s  version string at 0x20000: %r" % (sha(d), ver))
        return 0
    # Schematic: ROM1/IC37 = 0x000000, ROM2/IC79 = 0x200000, ROM3 = 0x400000.
    # ROM3 = style mask ROM IC93 (fitted on the user's unit; the schematic's
    # "not mounted" note on IC93 / IC43-flash alternative does not apply to it).
    parts = [("ic37_program1.bin", 0x000000), ("ic79_program2.bin", 0x200000),
             ("ic93_style.bin", 0x400000)]
    rc = 0
    prog = []
    for name, off in parts:
        lo = off - base
        chunk = d[lo:lo + 0x200000] if lo >= 0 else b""
        if len(chunk) < 0x200000:
            print("%s: not fully in this dump (%d of 2097152 bytes), skipped" % (name, len(chunk))); continue
        flag = " (ALL 0xFF - erased/unmapped?)" if chunk.count(0xFF) == len(chunk) else ""
        if name == "ic93_style.bin":
            sig = style_signature(chunk)
            flag += "  OS signature %s (sum 0x%04X, want 0xAA55)" % ("OK" if sig == 0xAA55 else "FAILED", sig)
            if sig != 0xAA55:
                rc = 1
        with open(os.path.join(a.out, name), "wb") as f:
            f.write(chunk)
        print("%-20s sha256 %s%s" % (name, sha(chunk), flag))
        if name != "ic93_style.bin":
            prog.append(chunk)
    if len(prog) == 2:                       # IC37 + IC79 = the 4 MB program flash, in CPU address order
        merged = prog[0] + prog[1]
        with open(os.path.join(a.out, "rs7000_program_merged.bin"), "wb") as f:
            f.write(merged)
        print("%-20s sha256 %s  (IC37 + IC79)" % ("rs7000_program_merged.bin", sha(merged)))
    if rc:
        print("\nStyle ROM signature failed: re-read IC93 (compare two dumps) before trusting it.")
    return rc


def lanes(d, endian):
    """Split a 32-bit-bus image into its two 16-bit chip lanes: (bytes 0-1 of every dword, bytes 2-3).
    endian 'big' keeps the byte order of the dump inside each half-word, 'little' swaps it."""
    n = len(d) // 4
    first, second = bytearray(n * 2), bytearray(n * 2)
    a, b = (0, 1) if endian == "big" else (1, 0)
    first[0::2], first[1::2] = d[a::4][:n], d[b::4][:n]
    second[0::2], second[1::2] = d[2 + a::4][:n], d[2 + b::4][:n]
    return bytes(first), bytes(second)


def carve_motif(a, d, base):
    """Original Motif.  Program flash = IC31 + IC25, 2 MB each, interleaved by 16-bit halves on the 32-bit bus
    (the OS's flash table is {0xA0000000, 0xA0000002}); IC31 = bytes 0-1 of every dword, IC25 = bytes 2-3
    (assumed from the table order = the test screen's label order; --swap-chips if a chip read says otherwise).
    Program mask ROM IC101 = 2 MB, a separate dump (mask_rom_*.bin) with the OS's own 0xAA55 signature."""
    rc = 0
    if a.mask or os.path.basename(a.flash).startswith("mask_rom_"):
        if len(d) < 0x200000:
            print("IC101 needs 2 MB (0x200000 bytes); this file has %d" % len(d)); return 2
        chunk = d[:0x200000]
        sig = style_signature(chunk)
        print("ic101_mask.bin       sha256 %s  OS signature %s (sum 0x%04X, want 0xAA55)"
              % (sha(chunk), "OK" if sig == 0xAA55 else "FAILED", sig))
        with open(os.path.join(a.out, "ic101_mask.bin"), "wb") as f:
            f.write(chunk)
        if sig != 0xAA55:
            print("\nIC101 signature failed: re-read it (compare two dumps) before trusting it."); rc = 1
        return rc
    if base != 0 or len(d) < 0x400000:
        print("the program flash needs the full 4 MB from CPU address 0 (cs0_flash_00000000.bin); got %d bytes at 0x%X"
              % (len(d), base)); return 2
    d = d[:0x400000]
    c31, c25 = lanes(d, a.chip_endian)
    if a.swap_chips:
        c31, c25 = c25, c31
    for name, data in (("ic31_program.bin", c31), ("ic25_program.bin", c25), ("motif_program_merged.bin", d)):
        with open(os.path.join(a.out, name), "wb") as f:
            f.write(data)
        print("%-24s %7d bytes  sha256 %s" % (name, len(data), sha(data)))
    print("version string at 0x20000: %r" % d[0x20000:0x20008])
    print("merged = the CPU's view of the 4 MB window; ic31 / ic25 = its two interleaved 16-bit chips (assignment assumed).")
    return rc


def cmd_split_wave(a):
    d = open(a.wave, "rb").read()          # u32 big-endian words as dumped
    n = len(d) // 4
    lo = bytearray(n * 2); hi = bytearray(n * 2)
    for i in range(n):
        w = int.from_bytes(d[i * 4:i * 4 + 4], "big")
        l, h = w & 0xFFFF, w >> 16
        lo[2 * i:2 * i + 2] = l.to_bytes(2, a.chip_endian)
        hi[2 * i:2 * i + 2] = h.to_bytes(2, a.chip_endian)
    first, second = (hi, lo) if a.swap else (lo, hi)
    os.makedirs(a.out, exist_ok=True)
    names = ((a.target + "_wave_lo16.bin", a.target + "_wave_hi16.bin") if a.target in ("es", "motif")
             else ("ic65_wave_l.bin", "ic68_wave_h.bin"))      # ES / Motif chip layout unknown: just the two halves
    for name, data in zip(names, (first, second)):
        with open(os.path.join(a.out, name), "wb") as f:
            f.write(data)
        print("%-16s %d bytes sha256 %s" % (name, len(data), sha(bytes(data))))
    print("NOTE: L=low half / H=high half and the chip byte order are assumptions; "
          "compare against a T76 read of IC65/IC68 and use --swap / --chip-endian / "
          "--half-order to match.")
    return 0


def pgm_segments_es(d):
    """Motif ES `MOTIF2 OS File` and original Motif `WSSx OS File`: entries of [8 bytes][u32 dest][u32 size] from 0x80 until
    dest == 0xFFFFFFFF; the data of all entries is the end of the file, back to back."""
    segs, o = [], 0x80
    while o + 16 <= len(d):
        dst, ln = int.from_bytes(d[o + 8:o + 12], "big"), int.from_bytes(d[o + 12:o + 16], "big")
        if dst == 0xFFFFFFFF or ln == 0:
            break
        segs.append((dst, ln)); o += 16
    return segs


def pgm_flash_es(path):
    d = open(path, "rb").read()
    segs = pgm_segments_es(d)
    off = len(d) - sum(ln for _, ln in segs)
    img = bytearray(b"\xff" * 0x400000)
    for dst, ln in segs:
        img[dst:dst + ln] = d[off:off + ln]; off += ln
    return bytes(img), segs


def pgm_flash(path):
    d = open(path, "rb").read()
    img = bytearray(b"\xff" * 0x400000)
    segs = [(0x00020000, 0x1E0000), (0x00200000, 0x1946C6)]
    off = 0x680
    for dst, ln in segs:
        img[dst:dst + ln] = d[off:off + ln]; off += ln
    return bytes(img), segs


def cmd_check_update(a):
    dump = open(a.flash, "rb").read()
    ref, segs = (pgm_flash_es if a.target in ("es", "motif") else pgm_flash)(a.pgm)
    ok = True
    for dst, ln in segs:
        d = dump[dst:dst + ln]; r = ref[dst:dst + ln]
        diff = [i for i in range(0, min(len(d), len(r)), 4096)
                if d[i:i + 4096] != r[i:i + 4096]]
        print("segment 0x%06X-0x%06X: %s" % (dst, dst + ln - 1,
              "IDENTICAL" if d == r else "%d of %d 4 KB blocks differ" %
              (len(diff), (ln + 4095) // 4096)))
        if d != r:
            ok = False
            n = next(i for i in range(min(len(d), len(r))) if d[i] != r[i])
            print("   first difference at 0x%06X: dump %02X, updater %02X" % (dst + n, d[n], r[n]))
    print("\nIf these are identical the capture path (addresses, byte order, validation) is "
          "proven for the program ROMs." if ok else
          "\nDifferences can mean a different OS version in your unit (check the version "
          "string at 0x20000) or a capture problem (look for the pattern of the differing blocks).")
    return 0 if ok else 1


def cmd_analyze_wave(a):
    import struct
    d = open(a.wave, "rb").read()
    n = min(len(d) // 4, a.words)
    def roughness(samples):
        s = 0
        for x, y in zip(samples, samples[1:]):
            s += abs(x - y)
        return s / max(1, len(samples) - 1)
    best = []
    start = a.skip_words
    words = [int.from_bytes(d[(start + i) * 4:(start + i) * 4 + 4], "big") for i in range(n)]
    for half in ("L,H", "H,L"):
        for order in ("be", "le"):
            seq = []
            for w in words:
                l, h = w & 0xFFFF, w >> 16
                pair = (l, h) if half == "L,H" else (h, l)
                for v in pair:
                    if order == "le":
                        v = ((v & 0xFF) << 8) | (v >> 8)
                    seq.append(v - 0x10000 if v & 0x8000 else v)
            best.append((roughness(seq), half, order))
    best.sort()
    print("mean |sample[i+1]-sample[i]| (smaller = smoother audio):")
    for r, half, order in best:
        print("  stream %s, 16-bit %s: %.1f" % (half, order, r))
    print("Only meaningful on a region that holds 16-bit PCM; try --skip-words at a few offsets.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    TGT = dict(choices=sorted(C.TARGETS), default="rs7000")
    p = sub.add_parser("mem-log"); p.add_argument("logs", nargs="+"); p.add_argument("--target", **TGT); p.add_argument("--out", default="dump/cs0_flash_rebuilt.bin")
    p.add_argument("--start", type=lambda x: int(x, 0), default=0); p.add_argument("--size", type=lambda x: int(x, 0), default=None)
    p = sub.add_parser("wave-log"); p.add_argument("logs", nargs="+"); p.add_argument("--target", **TGT); p.add_argument("--out", default="dump/wave_words_rebuilt.bin")
    p.add_argument("--first-word", type=lambda x: int(x, 0), default=0); p.add_argument("--words", type=lambda x: int(x, 0), default=None)
    p.add_argument("--half-order", choices=["hi_lo", "lo_hi"], default=None)
    p = sub.add_parser("carve"); p.add_argument("--target", **TGT); p.add_argument("flash"); p.add_argument("--out", default="dump/chips")
    p.add_argument("--mask", action="store_true", help="motif: the file is the IC101 mask ROM dump (also assumed for names mask_rom_*.bin)")
    p.add_argument("--swap-chips", action="store_true", help="motif: swap the IC31 / IC25 assignment")
    p.add_argument("--chip-endian", choices=["big", "little"], default="big",
                   help="motif: byte order inside each 16-bit chip word (big = as dumped, the same as the RS7000 files)")
    p.add_argument("--start", type=lambda x: int(x, 0), default=None,
                   help="CPU address of the first byte of FLASH (default: from the cs0_flash_XXXXXXXX.bin name, else 0)")
    p = sub.add_parser("split-wave"); p.add_argument("--target", **TGT); p.add_argument("wave"); p.add_argument("--out", default="dump/chips")
    p.add_argument("--swap", action="store_true"); p.add_argument("--chip-endian", choices=["little", "big"], default="little")
    p = sub.add_parser("check-update"); p.add_argument("--target", **TGT); p.add_argument("flash"); p.add_argument("pgm")
    p = sub.add_parser("analyze-wave"); p.add_argument("wave"); p.add_argument("--words", type=lambda x: int(x, 0), default=20000)
    p.add_argument("--skip-words", type=lambda x: int(x, 0), default=0x1000)
    a = C.apply_target(ap.parse_args(argv))
    return {"mem-log": cmd_mem_log, "wave-log": cmd_wave_log, "carve": cmd_carve,
            "split-wave": cmd_split_wave, "check-update": cmd_check_update,
            "analyze-wave": cmd_analyze_wave}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
