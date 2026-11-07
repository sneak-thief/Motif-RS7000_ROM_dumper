#!/usr/bin/env python3
# SPDX-License-Identifier: MIT  (see LICENSE)
"""Console dumper for the RS7000, Motif ES and original Motif (talks to the OS debug console on SCI0;
on the RS7000 that is header CN23).  Pick the unit with --target rs7000 (default) | es | motif.

  probe      - find the baud rate and show what the console says
  mem        - dump CPU-visible memory with `db` (program flash, + style ROM on the RS7000; --area mask = Motif IC101)
  wave       - dump wave ROM through the SWP with `wd`
  wave-probe - read one page at word addresses 2^k and show which ones repeat page 0 / are blank
               (helps to find the end of the wave ROM when its size is not known; needed for --target motif)
  verify     - re-read pages and compare them with a previous dump

Only these console commands are ever sent: db, wd (or wds/wn with --cmd),
?, Y/N answers and bare CR.  It never uses cb/cw/cl (memory change) or rs (reset).
Everything is validated per page; progress is saved so a run can be resumed.
Run `probe` first.  See README for wiring (3.3 V logic only!).
"""
import argparse, json, os, random, sys, time
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

SCAN_BAUDS = [38400, 39062, 31250, 57600, 19200, 9600, 115200]


def open_console(a, link=None):
    link = link or C.SerialLink(a.port, a.baud)
    if a.raw_log:
        os.makedirs(os.path.dirname(os.path.abspath(a.raw_log)), exist_ok=True)     # e.g. --raw-log wave/wave.log
    log = open(a.raw_log, "a", encoding="latin-1") if a.raw_log else None
    return C.Console(link, char_delay=a.char_delay, prompt_re=a.prompt_re,
                     raw_log=log, verbose=a.verbose, quiet=a.quiet)


# ------------------------------------------------------------------ probe
def cmd_probe(a):
    bauds = SCAN_BAUDS if a.scan else [a.baud]
    for baud in bauds:
        a.baud = baud
        print("== %d baud" % baud)
        try:
            con = open_console(a)
        except Exception as e:
            print("cannot open port:", e)
            return 1
        con.link.reset_input() if hasattr(con.link, "reset_input") else None
        con.send("\r")
        time.sleep(0.5)
        con.send("?\r")
        t_end = time.time() + 3.0
        while time.time() < t_end:
            con._pull()
        txt = con.buf
        printable = sum(1 for ch in txt if 32 <= ord(ch) < 127 or ch in "\r\n")
        ok = "CONSOLE FUNCTIONS" in txt or "Waveform List" in txt
        print("received %d chars, %d%% printable%s" %
              (len(txt), 100 * printable // max(1, len(txt)),
               "  <-- CONSOLE MENU SEEN" if ok else ""))
        print(txt[-600:] if txt else "(nothing)")
        con.link.close()
        if ok:
            print("\nUse:  --baud %d" % baud)
            return 0
    if a.target == "motif":
        print("No menu seen.  Check: 3.3 V adapter TX -> the CPU's RxD0 pin, RX <- TxD0, common GND, power on, "
              "and that the unit has finished booting (see README, \"Original Motif\").")
    else:
        print("No menu seen.  Check: 3.3 V adapter TX->CN23 pin3, RX<-pin2, GND, "
              "R148/R149 fitted or bridged, power on.")
    return 2


# -------------------------------------------------------------------- mem
def load_progress(path, key, restart):
    if restart or not os.path.exists(path):
        return {key: 0}
    with open(path) as f:
        d = json.load(f)
    d.setdefault(key, 0)
    return d


def save_progress(path, d):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, path)


def cmd_mem(a, link=None):
    os.makedirs(a.out, exist_ok=True)
    if a.area == "mask":
        mb = C.TARGETS[a.target]["mask_base"]
        if mb is None:
            print("--area mask exists only for --target motif (IC101).  RS7000: the style ROM IC93 is part of the 6 MB flash dump.")
            return 1
        a.base = mb
        out_path = os.path.join(a.out, "mask_rom_%08X.bin" % a.start)
    else:
        out_path = os.path.join(a.out, "cs0_flash_%08X.bin" % a.start)
    if a.start % C.PAGE_BYTES or a.length % C.PAGE_BYTES:
        print("start and length must be multiples of 0x100"); return 1
    prog_path = out_path + ".progress.json"
    npages = a.length // C.PAGE_BYTES
    prog = load_progress(prog_path, "next_page", a.restart)
    page = prog["next_page"]
    if not os.path.exists(out_path):
        with open(out_path, "wb") as f:
            f.truncate(a.length)
    con = open_console(a, link)
    # leave any half-finished command (e.g. a "More?" prompt from an interrupted run)
    con.resync()
    print("dumping %d pages (0x%X bytes) from CPU address 0x%08X -> %s, resuming at page %d"
          % (npages, a.length, a.base + a.start, out_path, page))
    t0 = time.time(); done0 = page; retries = 0; sess_fails = 0
    f = open(out_path, "r+b")
    try:
        while page < npages:
            addr = a.base + a.start + page * C.PAGE_BYTES
            try:
                con.mem_session(addr)
            except TimeoutError as e:
                sess_fails += 1; retries += 1
                print("\n! cannot start db session: %s" % e)
                if sess_fails > a.max_retries:
                    print("giving up; progress saved, rerun to resume"); return 3
                con.resync()
                continue
            sess_fails = 0
            fails = 0
            while page < npages:
                addr = a.base + a.start + page * C.PAGE_BYTES
                try:
                    data = con.mem_page(addr, a.timeout)
                except (TimeoutError, C.PageError) as e:
                    data = None; print("\n! %s" % e)
                if data is None:
                    fails += 1; retries += 1
                    print("\n! page %d (0x%08X) failed validation (%d)" % (page, addr, fails))
                    if fails > a.max_retries:
                        print("giving up; progress saved, rerun to resume"); return 3
                    con.resync()
                    break                       # restart a db session at the same page
                fails = 0
                f.seek(page * C.PAGE_BYTES); f.write(data)
                page += 1
                if page % 64 == 0:
                    f.flush(); prog["next_page"] = page; save_progress(prog_path, prog)
                    el = time.time() - t0; rate = (page - done0) / max(el, 1)
                    eta = (npages - page) / max(rate, 1e-9)
                    print("\r%5.1f%%  page %d/%d  %.1f pages/s  ETA %d:%02d  retries %d   "
                          % (100.0 * page / npages, page, npages, rate,
                             eta // 3600, eta % 3600 // 60, retries), end="", flush=True)
                if page < npages:
                    con.send("y\r")
                else:
                    con.send("n\r")
    except KeyboardInterrupt:
        print("\ninterrupted; progress saved")
    finally:
        f.flush(); f.close()
        prog["next_page"] = page; save_progress(prog_path, prog)
    print("\nmem dump %s (%d/%d pages)" % ("complete" if page >= npages else "incomplete", page, npages))
    return 0 if page >= npages else 4


# ------------------------------------------------------------------- wave
def need_words(a):
    """The wave ROM size is not known for every target (--target motif); refuse to guess."""
    if a.words is None:
        print("The wave ROM size of --target %s is not known: pass --words N (32-bit words = chip bytes in total / 4;\n"
              "e.g. 0x1800000 for 96 MB).  `wave-probe` shows where the ROM seems to end." % a.target)
        return False
    return True


def cmd_wave(a, link=None):
    if not need_words(a):
        return 2
    os.makedirs(a.out, exist_ok=True)
    npages = (a.words + C.WAVE_PAGE_WORDS - 1) // C.WAVE_PAGE_WORDS
    out_path = os.path.join(a.out, "wave_words_%s.bin" % a.half_order)
    prog_path = out_path + ".progress.json"
    prog = load_progress(prog_path, "next_page", a.restart)
    page = prog["next_page"]
    if not os.path.exists(out_path):
        with open(out_path, "wb") as f:
            f.truncate(a.words * 4)
    con = open_console(a, link)
    print("dumping %d words (%d pages) with `%s` -> %s, resuming at page %d"
          % (a.words, npages, a.cmd, out_path, page))
    t0 = time.time(); done0 = page; retries = 0
    f = open(out_path, "r+b")
    try:
        while page < npages:
            w0 = a.first_word + page * C.WAVE_PAGE_WORDS
            ok = False
            for attempt in range(a.max_retries + 1):
                try:
                    txt = con.wave_page_text(w0, a.cmd, a.wave_prompt, a.timeout, a.half_order)
                    words = C.assemble_wave_page(txt, w0, a.half_order)
                except (TimeoutError, C.PageError) as e:
                    words = None; print("\n! %s" % e)
                if words is not None:
                    ok = True; break
                retries += 1; con.resync()
            if not ok:
                print("\npage %d (word 0x%06X) failed %d times; progress saved" %
                      (page, w0, a.max_retries + 1)); return 3
            f.seek(page * C.WAVE_PAGE_WORDS * 4)
            f.write(b"".join(w.to_bytes(4, "big") for w in words))
            page += 1
            if page % 32 == 0:
                f.flush(); prog["next_page"] = page; save_progress(prog_path, prog)
                el = time.time() - t0; rate = (page - done0) / max(el, 1)
                eta = (npages - page) / max(rate, 1e-9)
                print("\r%5.1f%%  page %d/%d  %.2f pages/s  ETA %d:%02d  retries %d   "
                      % (100.0 * page / npages, page, npages, rate,
                         eta // 3600, eta % 3600 // 60, retries), end="", flush=True)
    except KeyboardInterrupt:
        print("\ninterrupted; progress saved")
    finally:
        f.flush(); f.close()
        prog["next_page"] = page; save_progress(prog_path, prog)
    print("\nwave dump %s (%d/%d pages)" % ("complete" if page >= npages else "incomplete", page, npages))
    return 0 if page >= npages else 4


def cmd_wave_probe(a, link=None):
    """Read one wd page at word address 0 and at 2^k (k = --min-bits .. --max-bits) and say what each looks like.

    Wave ROM that is not fitted / past the end typically reads blank (all 0000 or FFFF) or wraps around and
    repeats an earlier page.  The hardware decides; this only reports, so look at the table yourself:
    the size is the first power of two where the pages stop being real data (or start repeating page 0)."""
    con = open_console(a, link)
    con.resync()

    def page(w0):
        for attempt in range(a.max_retries + 1):
            try:
                txt = con.wave_page_text(w0, a.cmd, a.wave_prompt, a.timeout, a.half_order)
                words = C.assemble_wave_page(txt, w0, a.half_order)
                if words is not None:
                    return words
            except (TimeoutError, C.PageError) as e:
                print("! %s" % e)
            con.resync()
        return None

    base = page(a.first_word)
    if base is None:
        print("cannot read page 0"); return 3
    print("page at word 0x%08X: %s ..." % (a.first_word, " ".join("%08X" % w for w in base[:4])))
    print("%-12s %-12s  %s" % ("word addr", "byte addr", "first dwords / verdict"))
    for k in range(a.min_bits, a.max_bits + 1):
        w0 = 1 << k
        words = page(w0)
        if words is None:
            print("0x%08X   0x%08X   UNREADABLE" % (w0, w0 * 4)); continue
        if words == base:
            verdict = "SAME AS PAGE 0 (wrap-around?)"
        elif all(w == 0 for w in words):
            verdict = "all 0"
        elif all(w == 0xFFFFFFFF for w in words):
            verdict = "all FFFFFFFF (blank / not fitted?)"
        elif len(set(words)) == 1:
            verdict = "constant %08X" % words[0]
        else:
            verdict = "data"
        print("0x%08X   0x%08X   %s  %s" % (w0, w0 * 4, " ".join("%08X" % w for w in words[:4]), verdict))
    return 0


def cmd_verify(a, link=None):
    """Re-read N random pages (or all, --samples 0) and compare with an existing dump.

    Like `mem` and `wave`, every page is retried (with a resync) when the console glitches, and a page that
    differs from the file is re-read before it is counted as a mismatch, so one noisy line does not abort
    a multi-hour run or create a false mismatch.  Use --start-page N to continue an interrupted run."""
    con = open_console(a, link)
    with open(a.file, "rb") as f:
        img = f.read()
    con.resync()                                    # leave any half-finished command
    if a.kind == "mem":
        unit = C.PAGE_BYTES
    else:
        unit = C.WAVE_PAGE_WORDS * 4
    total = len(img) // unit
    if total == 0:
        print("%s is smaller than one page (%d bytes)" % (a.file, unit)); return 1
    if len(img) % unit:
        print("note: %d trailing bytes in %s are not a whole page and are not checked" % (len(img) % unit, a.file))
    pages = list(range(total)) if a.samples == 0 else sorted(random.sample(range(total), min(a.samples, total)))
    pages = [p for p in pages if p >= a.start_page]

    def read_page(p):
        """one page as bytes, or None after max_retries failed attempts"""
        for attempt in range(a.max_retries + 1):
            try:
                if a.kind == "mem":
                    addr = a.base + a.start + p * C.PAGE_BYTES
                    con.mem_session(addr)
                    data = con.mem_page(addr, a.timeout)
                    con.send("n\r"); con.drain(0.2)
                    return data
                w0 = a.first_word + p * C.WAVE_PAGE_WORDS
                txt = con.wave_page_text(w0, a.cmd, a.wave_prompt, a.timeout, a.half_order)
                words = C.assemble_wave_page(txt, w0, a.half_order)
                if words is not None:
                    return b"".join(w.to_bytes(4, "big") for w in words)
            except (TimeoutError, C.PageError) as e:
                print("\n! page %d: %s" % (p, e))
            con.resync()
        return None

    bad = unreadable = rereads = 0
    t0 = time.time()
    try:
        for n, p in enumerate(pages):
            ref = img[p * unit:(p + 1) * unit]
            data = read_page(p)
            if data is not None and data != ref:
                for _ in range(2):                  # a real difference repeats; line noise does not
                    again = read_page(p)
                    if again == ref:
                        data = again; rereads += 1; break
                    if again is not None:
                        data = again
            where = ("addr 0x%08X" % (a.base + a.start + p * unit)) if a.kind == "mem" else ("word 0x%06X" % (a.first_word + p * C.WAVE_PAGE_WORDS))
            if data is None:
                unreadable += 1; print("\nUNREADABLE page %d (%s)" % (p, where))
            elif data != ref:
                bad += 1; print("\nMISMATCH page %d (%s)" % (p, where))
            if (n + 1) % 32 == 0 or n + 1 == len(pages):
                el = time.time() - t0; rate = (n + 1) / max(el, 1e-9)
                eta = (len(pages) - n - 1) / max(rate, 1e-9)
                print("\r%5.1f%%  page %d  %.2f pages/s  ETA %d:%02d  mismatches %d  unreadable %d   "
                      % (100.0 * (n + 1) / len(pages), p, rate, eta // 3600, eta % 3600 // 60, bad, unreadable),
                      end="", flush=True)
    except KeyboardInterrupt:
        print("\ninterrupted at page %d; continue with --start-page %d" % (p, p))
        return 4
    print("\nverified %d pages, %d mismatches, %d unreadable (%d noisy reads fixed by re-reading)"
          % (len(pages), bad, unreadable, rereads))
    return 0 if bad == 0 and unreadable == 0 else 5


def main(argv=None, link=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd_name", required=True)
    def common(p):
        p.add_argument("--target", choices=sorted(C.TARGETS), default="rs7000",
                       help="rs7000 (default), es (Motif ES) or motif (original Motif 6/7/8): sets default sizes, wave half order, output dir")
        p.add_argument("--port", default="/dev/ttyUSB0")
        p.add_argument("--baud", type=int, default=38400)
        p.add_argument("--char-delay", type=float, default=0.003, help="seconds between typed characters")
        p.add_argument("--prompt-re", default=C.DEFAULT_PROMPT_RE)
        p.add_argument("--raw-log", help="append everything received to this text file")
        p.add_argument("--timeout", type=float, default=10.0)
        p.add_argument("--max-retries", type=int, default=5)
        p.add_argument("--verbose", action="store_true")
        p.add_argument("--quiet", type=float, default=0.3, help="idle time that counts as end of output when resyncing")
    p = sub.add_parser("probe"); common(p); p.add_argument("--scan", action="store_true")
    p = sub.add_parser("mem"); common(p)
    p.add_argument("--out", default=None)
    p.add_argument("--base", type=lambda x: int(x, 0), default=0xA0000000,
                   help="CPU address of physical 0 (0xA0000000 = uncached P2, 0x80000000 = cached P1)")
    p.add_argument("--start", type=lambda x: int(x, 0), default=0)
    p.add_argument("--length", type=lambda x: int(x, 0), default=None, help="bytes (default: 6 MB rs7000, 4 MB es and motif)")
    p.add_argument("--area", choices=["flash", "mask"], default="flash",
                   help="flash (default): program flash at CPU 0xA0000000.  mask: program mask ROM IC101 of --target motif "
                        "(2 MB at CPU 0xB0000000, file mask_rom_00000000.bin)")
    p.add_argument("--restart", action="store_true")
    p = sub.add_parser("wave"); common(p)
    p.add_argument("--out", default=None)
    p.add_argument("--cmd", default="wd")
    p.add_argument("--wave-prompt", default=C.WAVE_PROMPT)
    p.add_argument("--first-word", type=lambda x: int(x, 0), default=0)
    p.add_argument("--words", type=lambda x: int(x, 0), default=None, help="32-bit words (default 0x400000 = 16 MB rs7000, 0x1800000 = 96 MB es; motif: required)")
    p.add_argument("--half-order", choices=["hi_lo", "lo_hi"], default=None)
    p.add_argument("--restart", action="store_true")
    p = sub.add_parser("wave-probe"); common(p)
    p.add_argument("--cmd", default="wd"); p.add_argument("--wave-prompt", default=C.WAVE_PROMPT)
    p.add_argument("--first-word", type=lambda x: int(x, 0), default=0)
    p.add_argument("--min-bits", type=int, default=16, help="first probe address is 2^MIN_BITS words (default 16)")
    p.add_argument("--max-bits", type=int, default=25, help="last probe address is 2^MAX_BITS words (default 25 = 128 MB)")
    p.add_argument("--half-order", choices=["hi_lo", "lo_hi"], default=None)
    p = sub.add_parser("verify"); common(p)
    p.add_argument("kind", choices=["mem", "wave"]); p.add_argument("file")
    p.add_argument("--samples", type=int, default=200, help="0 = every page")
    p.add_argument("--start-page", type=int, default=0, help="skip pages below this number (continue an interrupted run)")
    p.add_argument("--base", type=lambda x: int(x, 0), default=0xA0000000)
    p.add_argument("--start", type=lambda x: int(x, 0), default=0)
    p.add_argument("--cmd", default="wd"); p.add_argument("--wave-prompt", default=C.WAVE_PROMPT)
    p.add_argument("--first-word", type=lambda x: int(x, 0), default=0)
    p.add_argument("--half-order", choices=["hi_lo", "lo_hi"], default=None)
    a = C.apply_target(ap.parse_args(argv))
    if a.cmd_name == "probe": return cmd_probe(a)
    if a.cmd_name == "mem": return cmd_mem(a, link)
    if a.cmd_name == "wave": return cmd_wave(a, link)
    if a.cmd_name == "wave-probe": return cmd_wave_probe(a, link)
    return cmd_verify(a, link)


if __name__ == "__main__":
    sys.exit(main())
