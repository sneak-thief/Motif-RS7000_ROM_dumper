#!/usr/bin/env python3
# SPDX-License-Identifier: MIT  (see LICENSE)
"""Self-test: a simulated RS7000 console (built from the firmware's format strings)
driving the real dumper and parser code.  Proves the tooling logic, NOT the
hardware -- real output may differ in small ways (see README)."""
import importlib.util, os, random, sys, tempfile, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
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

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

dumper = load("dumper", "rs7k-motif_dumper.py")
parser = load("parser", "rs7k-motif_dump-parser.py")


class Sparse:
    """Memory with holes: slices outside the given regions read as empty (the sim pads with 0xFF)."""
    def __init__(self, regions): self.regions = regions            # [(physical base, bytes)]
    def __getitem__(self, sl):
        for b, data in self.regions:
            if b <= sl.start < b + len(data):
                return data[sl.start - b:sl.stop - b]
        return b""


class SimLink:
    """Console emulation.  Output text is queued; read() returns what is queued."""
    def __init__(self, mem, wave, noise=0.0, seed=1, flips=True, poison=(), hi_first=False):
        self.mem, self.wave = mem, wave          # bytes / list of u32
        self.hi_first = hi_first                 # ES: first printed half-word is the HIGH half
        self.noise = noise; self.flips = flips; self.poison = set(poison); self.rnd = random.Random(seed)
        self.out = []; self.line = ""; self.state = "idle"
        self.addr = 0; self.out.append("CNS >")          # real idle prompt is "CNS >"
    def say(self, s):
        if self.noise and len(s) > 20:
            t = list(s)
            for i in range(len(t)):
                r = self.rnd.random()
                if r < self.noise / 2: t[i] = ""                      # dropped char
                elif self.flips and r < self.noise: t[i] = self.rnd.choice("0123456789ABCDEF")  # flipped
            s = "".join(t)
        self.out.append(s)
    def write(self, b):
        for ch in b.decode("latin-1"):
            self.say(ch) if False else self.out.append(ch)            # echo (noise-free)
            if ch in "\r\n":
                self.out.append("\n"); self.handle(self.line); self.line = ""
            else:
                self.line += ch
    def read(self):
        if not self.out: return b""
        s = "".join(self.out); self.out = []
        return s.encode("latin-1")
    def close(self): pass
    def reset_input(self): self.out = []
    # ---- device behaviour
    def handle(self, line):
        line = line.strip()
        if self.state == "idle":
            if line == "db": self.state = "db_addr"; self.out.append("Address ? : 0x")
            elif line == "wd": self.state = "wd_addr"; self.out.append(C.WAVE_PROMPT)
            elif line == "?": self.out.append("| CONSOLE FUNCTIONS |\n"); self.prompt()
            else: self.prompt()
        elif self.state == "db_addr":
            self.addr = int(line, 16); self.state = "more"; self.db_page()
        elif self.state == "more":
            if line[:1] in "Yy": self.db_page()
            elif line[:1] in "Nn": self.state = "idle"; self.prompt()
            else: self.out.append("More? (Y/N) : ")
        elif self.state == "wd_addr":
            self.state = "idle"; self.wd_page(int(line, 16))
    def prompt(self): self.out.append("CNS >")
    def db_page(self):
        a = self.addr
        # layout as printed by the firmware: header, 60 dashes, 16 lines, then "More?" directly
        s = "Address  " + " ".join("%.2X" % ((a + i) & 0xFF) for i in range(16)) + "\n" + "-" * 60 + "\n"
        for l in range(16):
            la = a + 16 * l; phys = la & 0x1FFFFFFF
            d = self.mem[phys:phys + 16].ljust(16, b"\xff")
            s += "%.8X " % (la & ~0xF) + "".join("%.2X " % x for x in d)
            s += "".join(chr(x) if 32 <= x <= 126 else " " for x in d) + "\n"
        if a in self.poison:
            self.poison.discard(a)
            lines = s.split("\n")
            for i, ln in enumerate(lines):
                if ln[:8].isalnum() and i > 4:
                    for k in range(9, 9 + 48, 3):
                        b = int(ln[k:k + 2], 16)
                        if 32 <= b <= 126:
                            ln = ln[:k] + ("0" if ln[k] != "0" else "1") + ln[k + 1:]
                            lines[i] = ln; break
                    else:
                        continue
                    break
            s = "\n".join(lines)
        self.say(s); self.addr = a + 256
        self.out.append("More? (Y/N) : ")
    def wd_page(self, w0):
        s = "Address  0000 0000 0001 0001 0002 0002 0003 0003\n" + "-" * 48 + "\n"
        for l in range(32):
            wa = w0 + 4 * l
            s += "%.8X " % wa
            for w in self.wave[wa:wa + 4]:
                if self.hi_first:                             # ES: [reg 0xB8085CFC (high), reg 0xB8085CFE (low)]
                    s += "%.4X %.4X " % (w >> 16, w & 0xFFFF)
                else:                                         # RS7000: [reg 0x29E (low), reg 0x29C (high)]
                    s += "%.4X %.4X " % (w & 0xFFFF, w >> 16)
            s += "\n"
        self.say(s); self.prompt()


def run():
    tmp = tempfile.mkdtemp(prefix="rs7ksim")
    flash = open(os.environ.get("FLASH_BIN", "flash.bin"), "rb").read()
    rnd = random.Random(7)
    style = bytearray(rnd.getrandbits(8) for _ in range(0x200000))
    # make the style image carry the signature the OS self-test looks for (sum == 0xAA55)
    style[0x1FFFFE:0x200000] = b"\x00\x00"
    style[0x1FFFFE:0x200000] = ((0xAA55 - parser.style_signature(bytes(style))) & 0xFFFF).to_bytes(2, "big")
    style = bytes(style)
    assert parser.style_signature(style) == 0xAA55
    mem = flash[:0x400000] + style
    wave = [rnd.getrandbits(32) for _ in range(0x20000)]       # 128K words, 1024 pages

    # ---- memory dump, with noise, full 6 MB
    import argparse
    log = os.path.join(tmp, "mem.log")
    argv = ["mem", "--out", tmp, "--raw-log", log, "--timeout", "2", "--char-delay", "0", "--quiet", "0"]
    link = SimLink(mem, wave, noise=2e-6, flips=False, poison=[0x330C00 + 0xA0000000, 0xA0000000 + 0x10000])
    rc = dumper.main(argv, link)
    out = open(os.path.join(tmp, "cs0_flash_00000000.bin"), "rb").read()
    print("mem rc", rc, "identical to source:", out == mem)
    assert rc == 0 and out == mem

    # ---- parser: rebuild from the transcript, carve, compare with the updater
    rebuilt = os.path.join(tmp, "rebuilt.bin")
    parser.main(["mem-log", log, "--out", rebuilt])
    assert open(rebuilt, "rb").read() == mem
    parser.main(["carve", rebuilt, "--out", os.path.join(tmp, "chips")])
    assert parser.main(["carve", rebuilt, "--out", os.path.join(tmp, "chips")]) == 0   # signature OK
    c = os.path.join(tmp, "chips")
    assert open(os.path.join(c, "rs7000_program_merged.bin"), "rb").read() == mem[:0x400000] == \
        open(os.path.join(c, "ic37_program1.bin"), "rb").read() + open(os.path.join(c, "ic79_program2.bin"), "rb").read(), "RS7000 merged"
    pgm = os.environ.get("PGM", "/mnt/user-data/uploads/0532OS_.PGM")
    if os.path.exists(pgm):
        assert parser.main(["check-update", rebuilt, pgm]) == 0
    else:
        print("(set PGM=path/to/0532OS_.PGM to include the updater comparison)")

    # ---- IC93 only (what you would dump to read just the style ROM), then carve it
    link = SimLink(mem, wave)
    rc = dumper.main(["mem", "--out", tmp, "--start", "0x400000", "--length", "0x200000", "--timeout", "2",
                      "--char-delay", "0", "--quiet", "0"], link)
    ic93 = open(os.path.join(tmp, "cs0_flash_00400000.bin"), "rb").read()
    assert rc == 0 and ic93 == style
    assert parser.main(["carve", os.path.join(tmp, "cs0_flash_00400000.bin"), "--out", os.path.join(tmp, "c93")]) == 0
    assert open(os.path.join(tmp, "c93", "ic93_style.bin"), "rb").read() == style
    # a corrupted style ROM must fail the signature
    bad = bytearray(style); bad[0x100000] ^= 1
    open(os.path.join(tmp, "cs0_flash_00400000.bin"), "wb").write(bytes(bad))
    assert parser.main(["carve", os.path.join(tmp, "cs0_flash_00400000.bin"), "--out", os.path.join(tmp, "cbad")]) == 1
    # a stale "More?" prompt left by an interrupted run must not break a resumed dump
    link = SimLink(mem, wave); link.state = "more"
    rc = dumper.main(["mem", "--out", os.path.join(tmp, "r"), "--start", "0x400000", "--length", "0x1000", "--restart",
                      "--timeout", "2", "--char-delay", "0", "--quiet", "0"], link)
    assert rc == 0 and open(os.path.join(tmp, "r", "cs0_flash_00400000.bin"), "rb").read() == style[:0x1000]

    # ---- wave dump, with noise
    wlog = os.path.join(tmp, "wave.log")
    link = SimLink(mem, wave, noise=1e-5, seed=3, flips=False)
    rc = dumper.main(["wave", "--out", tmp, "--words", hex(len(wave)), "--raw-log", wlog,
                      "--timeout", "2", "--char-delay", "0", "--quiet", "0"], link)
    got = open(os.path.join(tmp, "wave_words_lo_hi.bin"), "rb").read()
    ref = b"".join(w.to_bytes(4, "big") for w in wave)
    print("wave rc", rc, "identical:", got == ref)
    assert rc == 0 and got == ref
    parser.main(["split-wave", os.path.join(tmp, "wave_words_lo_hi.bin"), "--out", os.path.join(tmp, "chips")])
    # ---- --raw-log in a folder that does not exist yet (the dumper must create it) and a nested --out
    nd = os.path.join(tmp, "newdir", "wave"); nl = os.path.join(nd, "wave.log")
    assert dumper.main(["wave", "--out", os.path.join(tmp, "o2", "wave"), "--words", "0x400", "--raw-log", nl,
                        "--timeout", "2", "--char-delay", "0", "--quiet", "0"], SimLink(mem, wave)) == 0
    assert os.path.getsize(nl) > 0 and os.path.exists(os.path.join(tmp, "o2", "wave", "wave_words_lo_hi.bin"))
    # ---- verify: clean, noisy (retries / re-reads), and a real difference
    vf = os.path.join(tmp, "wave_words_lo_hi.bin")
    vargs = ["--timeout", "2", "--char-delay", "0", "--quiet", "0"]
    assert dumper.main(["verify", "wave", vf, "--samples", "0"] + vargs, SimLink(mem, wave)) == 0, "verify clean"
    assert dumper.main(["verify", "wave", vf, "--samples", "0"] + vargs, SimLink(mem, wave, noise=1e-5, seed=11, flips=True)) == 0, "verify noisy"
    assert dumper.main(["verify", "wave", vf, "--samples", "20", "--start-page", "500"] + vargs, SimLink(mem, wave)) == 0, "verify --start-page"
    w2 = list(wave); w2[5 * C.WAVE_PAGE_WORDS + 3] ^= 0x10          # one flipped digit on page 5
    assert dumper.main(["verify", "wave", vf, "--samples", "0"] + vargs, SimLink(mem, w2)) == 5, "verify must flag a real difference"
    mf = os.path.join(tmp, "cs0_flash_00000000.bin")
    assert dumper.main(["verify", "mem", mf, "--samples", "40"] + vargs, SimLink(mem, wave)) == 0, "verify mem"
    # resume test: delete progress at 50 %, rerun, must still match

    # ---- Motif ES target: 4 MB program flash (incl. version string at 0x20000), wave hi_lo
    es_bin = os.environ.get("ES_FLASH_BIN")
    es = open(es_bin, "rb").read() if es_bin and os.path.exists(es_bin) else \
        (b"\xff" * 0x20000 + b"1.08".ljust(16) + bytes(rnd.getrandbits(8) for _ in range(0x1000)) ).ljust(0x400000, b"\xff")
    es = es[:0x400000].ljust(0x400000, b"\xff")
    ew = [rnd.getrandbits(32) for _ in range(0x20000)]
    link = SimLink(es, ew, hi_first=True)
    out = os.path.join(tmp, "es")
    rc = dumper.main(["mem", "--target", "es", "--out", out, "--timeout", "2", "--char-delay", "0", "--quiet", "0"], link)
    got = open(os.path.join(out, "cs0_flash_00000000.bin"), "rb").read()
    assert rc == 0 and got == es and got[0x20000:0x20004] == b"1.08", "ES mem dump"
    assert parser.main(["carve", "--target", "es", os.path.join(out, "cs0_flash_00000000.bin"), "--out", os.path.join(out, "c")]) == 0
    espgm = os.environ.get("ES_PGM", "/tmp/claude-0/-home-claude/eb3f2bfe-bf22-5404-ba4c-cd957f70c9f6/scratchpad/moes/0745OS_.PGM")
    if es_bin and os.path.exists(es_bin) and os.path.exists(espgm):
        assert parser.main(["check-update", "--target", "es", os.path.join(out, "cs0_flash_00000000.bin"), espgm]) == 0
    else:
        print("(set ES_FLASH_BIN and ES_PGM to include the ES updater comparison)")
    link = SimLink(es, ew, noise=1e-5, seed=5, flips=False, hi_first=True)
    wl = os.path.join(out, "wave.log")
    rc = dumper.main(["wave", "--target", "es", "--out", out, "--words", hex(len(ew)), "--raw-log", wl,
                      "--timeout", "2", "--char-delay", "0", "--quiet", "0"], link)
    got = open(os.path.join(out, "wave_words_hi_lo.bin"), "rb").read()
    assert rc == 0 and got == b"".join(w.to_bytes(4, "big") for w in ew), "ES wave dump"
    assert parser.main(["wave-log", wl, "--target", "es", "--words", hex(len(ew)), "--out", os.path.join(out, "wr.bin")]) == 0
    assert open(os.path.join(out, "wr.bin"), "rb").read() == got
    # defaults: 96 MB / 0x1800000 words, hi_lo
    import argparse
    ns = C.apply_target(argparse.Namespace(target="es", words=None, half_order=None, length=None, out=None))
    assert (ns.words, ns.half_order, ns.length, ns.out) == (0x1800000, "hi_lo", 0x400000, "dump_es")

    # ---- original Motif target (V1.90 "WSSx" OS): 4 MB flash window, wave lo_hi like the RS7000, no wave size default
    mpgm = os.environ.get("MOTIF_PGM", "0638OS_.PGM")
    if os.path.exists(mpgm):
        mflash, msegs = parser.pgm_flash_es(mpgm)
        assert [(hex(x), hex(y)) for x, y in msegs] == [("0x20000", "0x1e0000"), ("0x200000", "0x1feabc")], "motif PGM segments"
        mflash = bytes(b"\x00" * 0x20000) + mflash[0x20000:]               # the 128 KB kernel/boot area is not in the updater
    else:
        print("(set MOTIF_PGM=path/to/0638OS_.PGM to use the real Motif V1.90 updater)")
        mflash = (b"\x00" * 0x20000 + b"1.90".ljust(16, b" ") + bytes(rnd.getrandbits(8) for _ in range(0x2000))).ljust(0x400000, b"\xff")
    mw = [rnd.getrandbits(32) for _ in range(0x10000)]
    mw = mw + mw[:0x10000] + [0xFFFFFFFF] * 0x20000                      # data, a copy of it (wrap-around), blank
    out = os.path.join(tmp, "motif")
    rc = dumper.main(["mem", "--target", "motif", "--out", out, "--timeout", "2", "--char-delay", "0", "--quiet", "0"],
                     SimLink(mflash, mw))
    mgot = open(os.path.join(out, "cs0_flash_00000000.bin"), "rb").read()
    assert rc == 0 and mgot == mflash and mgot[0x20000:0x20004] == b"1.90", "Motif mem dump"
    cdir = os.path.join(out, "c")
    assert parser.main(["carve", "--target", "motif", os.path.join(out, "cs0_flash_00000000.bin"), "--out", cdir]) == 0
    assert open(os.path.join(cdir, "motif_program_merged.bin"), "rb").read() == mgot
    c31 = open(os.path.join(cdir, "ic31_program.bin"), "rb").read(); c25 = open(os.path.join(cdir, "ic25_program.bin"), "rb").read()
    assert len(c31) == len(c25) == 0x200000
    assert c31[:2] == mgot[0:2] and c31[2:4] == mgot[4:6] and c25[:2] == mgot[2:4] and c25[2:4] == mgot[6:8], "Motif lanes"
    re_il = bytearray(0x400000)
    for i in range(0, 0x400000, 4):
        re_il[i:i + 2] = c31[i // 2:i // 2 + 2]; re_il[i + 2:i + 4] = c25[i // 2:i // 2 + 2]
    assert bytes(re_il) == mgot, "re-interleaving the chip files must give the merged image"
    parser.main(["carve", "--target", "motif", "--swap-chips", os.path.join(out, "cs0_flash_00000000.bin"), "--out", cdir + "s"])
    assert open(os.path.join(cdir + "s", "ic31_program.bin"), "rb").read() == c25
    # mask ROM IC101: 2 MB at CPU 0xB0000000, dumped with --area mask, carved with the OS signature check
    mask = bytearray(rnd.getrandbits(8) for _ in range(0x200000)); mask[0x1FFFFE:0x200000] = b"\x00\x00"
    mask[0x1FFFFE:0x200000] = ((0xAA55 - parser.style_signature(bytes(mask))) & 0xFFFF).to_bytes(2, "big"); mask = bytes(mask)
    msim = Sparse([(0, mflash), (0x10000000, mask)])
    rc = dumper.main(["mem", "--target", "motif", "--area", "mask", "--out", out, "--length", "0x10000", "--timeout", "2",
                      "--char-delay", "0", "--quiet", "0"], SimLink(msim, mw))
    assert rc == 0 and open(os.path.join(out, "mask_rom_00000000.bin"), "rb").read() == mask[:0x10000], "mask ROM dump"
    rc = dumper.main(["mem", "--target", "motif", "--area", "mask", "--out", out, "--start", "0x10000", "--length", "0x1F0000",
                      "--timeout", "2", "--char-delay", "0", "--quiet", "0"], SimLink(msim, mw))
    assert rc == 0
    full = open(os.path.join(out, "mask_rom_00000000.bin"), "rb").read() + open(os.path.join(out, "mask_rom_00010000.bin"), "rb").read()
    assert full == mask
    open(os.path.join(out, "mask_rom_full.bin"), "wb").write(mask)
    assert parser.main(["carve", "--target", "motif", "--mask", os.path.join(out, "mask_rom_full.bin"), "--out", cdir]) == 0
    assert open(os.path.join(cdir, "ic101_mask.bin"), "rb").read() == mask
    open(os.path.join(out, "mask_rom_bad.bin"), "wb").write(mask[:0x100000] + bytes([mask[0x100000] ^ 1]) + mask[0x100001:])
    assert parser.main(["carve", "--target", "motif", os.path.join(out, "mask_rom_bad.bin"), "--out", cdir + "b"]) == 1
    assert dumper.main(["mem", "--target", "rs7000", "--area", "mask", "--out", out, "--timeout", "2"], SimLink(msim, mw)) == 1
    if os.path.exists(mpgm):
        assert parser.main(["check-update", "--target", "motif", os.path.join(out, "cs0_flash_00000000.bin"), mpgm]) == 0, "Motif check-update"
        bad = bytearray(mgot); bad[0x2DDA07] ^= 0x10
        open(os.path.join(out, "bad.bin"), "wb").write(bytes(bad))
        assert parser.main(["check-update", "--target", "motif", os.path.join(out, "bad.bin"), mpgm]) == 1, "Motif check-update must flag a difference"
    # no wave size default: `wave` must refuse without --words, and work with it (lo_hi file name, dump_motif default dir)
    assert dumper.main(["wave", "--target", "motif", "--out", out, "--timeout", "2", "--char-delay", "0", "--quiet", "0"], SimLink(mflash, mw)) == 2
    rc = dumper.main(["wave", "--target", "motif", "--out", out, "--words", "0x4000", "--timeout", "2", "--char-delay", "0", "--quiet", "0"],
                     SimLink(mflash, mw, noise=1e-5, seed=9, flips=False))
    mwg = open(os.path.join(out, "wave_words_lo_hi.bin"), "rb").read()
    assert rc == 0 and mwg == b"".join(w.to_bytes(4, "big") for w in mw[:0x4000]), "Motif wave dump"
    ns = C.apply_target(argparse.Namespace(target="motif", words=None, half_order=None, length=None, out=None))
    assert (ns.words, ns.half_order, ns.length, ns.out) == (None, "lo_hi", 0x400000, "dump_motif")
    # wave-probe reports data / wrap-around / blank
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = dumper.main(["wave-probe", "--target", "motif", "--min-bits", "14", "--max-bits", "17", "--timeout", "2",
                          "--char-delay", "0", "--quiet", "0"], SimLink(mflash, mw))
    rows = {l.split()[0]: l for l in buf.getvalue().splitlines() if l.startswith("0x")}
    assert rc == 0 and rows["0x00004000"].endswith("data") and rows["0x00008000"].endswith("data"), buf.getvalue()
    assert "SAME AS PAGE 0" in rows["0x00010000"] and "all FFFFFFFF" in rows["0x00020000"], buf.getvalue()
    shutil.rmtree(tmp)
    print("ALL SIMULATION TESTS PASSED")

if __name__ == "__main__":
    run()
