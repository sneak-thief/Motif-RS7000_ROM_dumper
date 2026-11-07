#!/usr/bin/env python3
# SPDX-License-Identifier: MIT  (see LICENSE)
"""Shared code for the RS7000 console dumper / parser.

Everything here is derived from the console code in the v1.22 OS image
(format strings and control flow of the `db` and `wd` commands), and has been
confirmed on a real RS7000 (full flash and wave dumps).  The Motif ES and Motif
settings below are still untested on hardware -- run `probe` first.

Console facts (from the firmware):
  db  : prints "Address ? : 0x", reads a hex string (sscanf "%x"), then dumps
        256 bytes = 16 lines of
            "%.8X " + 16 x "%.2X " + 16 ASCII chars ('.' -> ' ' if not 0x20..0x7E)
        and asks "More? (Y/N) : ".  Y/y continues with the next 256 bytes,
        N/n returns to the prompt.
  wd  : prints "Wave Address (SWP 32bit Address) ? : 0x", reads a hex string,
        reads 256 half-words from the SWP30 "wave direct access" registers and
        prints 32 lines of  "%.8X " + 8 x "%.4X "  (4 dwords per line, the
        address is a 32-bit DWORD address).  No "More?" prompt; one page/command.
        The OS read routine (ROM 0x1955CC) stores every dword as
        [reg 0x29E, reg 0x29C].  SWP30 register pairs put the HIGH half at the
        lower offset (address: 0x11C high, 0x11E low), so the first printed half-word
        of each dword is most likely the LOW half -> default half order "lo_hi".
        This is inferred, not measured: confirm with `analyze-wave`; a wrong guess is
        recoverable (`split-wave --swap`).
"""
import re, sys, time

ADDR_PROMPT = "Address ? : 0x"
WAVE_PROMPT = "Wave Address (SWP 32bit Address) ? : 0x"
MORE_PROMPT = "More? (Y/N) : "
# The idle prompt looks like  CNS> / CNS*>  (strings "CNS" "*" " " ">" in the OS);
# `probe` shows the real one.
DEFAULT_PROMPT_RE = r"CNS\S{0,2}\s*>\s*$"

MEM_LINE = re.compile(r"^([0-9A-F]{8}) ((?:[0-9A-F]{2} ){16})(.*)$")
WAVE_LINE = re.compile(r"^([0-9A-F]{8}) ((?:[0-9A-F]{4} ){8})")

# Per-unit defaults.  `--target` on the tools picks one.
#   rs7000: program flash 6 MB (CS0, incl. style ROM IC93), wave ROM 16 MB (0x400000 dwords).
#   es    : Motif ES.  The console, `db` and `wd` text formats are the same as the RS7000's
#           (checked in the 1.08 OS image), but `wd` reads a different chip: its read routine (ROM 0x28EDD4)
#           writes the 32-bit dword address to registers 0xB80848FC (high 16) / 0xB80848FE (low 16) and
#           stores every dword as [reg 0xB8085CFC, reg 0xB8085CFE].  By the same high-at-lower-offset
#           convention the first printed half-word is therefore the HIGH half -> default "hi_lo"
#           (inferred, not measured).  96 MB wave ROM = 0x1800000 dwords (size as given by the
#           user; not derivable from the OS image).  Program flash: the 1.08 OS ends at 0x3F5C01, so
#           0x400000 (4 MB) is the assumed size -- override with --length.
#   motif : original Motif 6/7/8 (V1.90 OS image `0638OS_.PGM`, "WSSx" firmware).  Same console (SCI0, SCBRR = 15,
#           so baud = Pphi/512 like the RS7000) and same `db` / `wd` text formats.  `wd` reads the wave ROM
#           with the RS7000's register protocol (address high/low -> +0x11C/+0x11E, length -> +0x19C/+0x19E,
#           command -> +0x21C, data from +0x29C/+0x29E, each dword stored [reg 0x29E, reg 0x29C]) but at base
#           0xB4000000 instead of 0xB80848xx -> low half first, `lo_hi`, like the RS7000.  The OS's own flash
#           address check accepts 0x000000-0x3FFFFF and 0xA0000000-0xA03FFFFF: 4 MB.  The wave ROM size is NOT
#           in the OS (the test screen names six wave ROMs IC-1..IC-6, the size of each is not shown), so there
#           is no default: `wave` needs --words, `wave-probe` helps to find it.
#   The Motif's two program flash chips (IC31, IC25) are interleaved on a 32-bit bus (the OS's flash table is
#   {0xA0000000, 0xA0000002}; the RS7000's is {0xA0000000, 0xA0200000}, i.e. consecutive), and its program mask
#   ROM IC101 is a 2 MB chip at 0x10000000 (CS4; dump it with `mem --area mask`, CPU address 0xB0000000).
TARGETS = {
    "rs7000": dict(half_order="lo_hi", wave_words=0x400000, mem_length=0x600000, out="dump",
                   mask_base=None, mask_length=None),
    "es":     dict(half_order="hi_lo", wave_words=0x1800000, mem_length=0x400000, out="dump_es",
                   mask_base=None, mask_length=None),
    "motif":  dict(half_order="lo_hi", wave_words=None,      mem_length=0x400000, out="dump_motif",
                   mask_base=0xB0000000, mask_length=0x200000),
}


def apply_target(a):
    """Fill in option values the user left unset from the chosen target."""
    t = TARGETS[getattr(a, "target", "rs7000")]
    mem_len = "mask_length" if getattr(a, "area", "flash") == "mask" else "mem_length"
    for name, key in (("half_order", "half_order"), ("words", "wave_words"),
                      ("length", mem_len), ("size", "mem_length"), ("out", "out")):
        if hasattr(a, name) and getattr(a, name) is None:
            setattr(a, name, t[key])
    return a


PAGE_BYTES = 256            # one db page
C_PAGE = PAGE_BYTES
WAVE_PAGE_WORDS = 128       # one wd page (256 half-words)


class PageError(Exception):
    pass


MORE_AT_LINE_START = re.compile(r"^More\? \(Y/N\) : ", re.M)


# ----------------------------------------------------------------- parsing
def ascii_ok(data: bytes, col: str) -> bool:
    """The ASCII column repeats the 16 bytes; use it as a per-line checksum."""
    col = col.rstrip("\r\n").ljust(16)[:16]
    for b, c in zip(data, col):
        want = chr(b) if 0x20 <= b <= 0x7E else " "
        if c != want:
            return False
    return True


def parse_mem_lines(text: str):
    """Yield (address, 16 bytes) for every internally consistent db line."""
    for raw in text.replace("\r", "\n").split("\n"):
        # a line may be preceded by echo / prompt residue: look for the address
        m = MEM_LINE.search(raw) if len(raw) >= 8 + 1 + 48 else None
        if not m:
            continue
        # anchor at the start of the 8-digit address (search() may start late)
        start = m.start()
        if start and re.match(r"[0-9A-F]", raw[start - 1]):
            continue
        try:
            data = bytes(int(x, 16) for x in m.group(2).split())
        except ValueError:
            continue
        if len(data) != 16 or not ascii_ok(data, m.group(3)):
            continue
        yield int(m.group(1), 16), data


def parse_wave_lines(text: str, half_order="lo_hi"):
    """Yield (word_address, [4 x u32]) for every well-formed wd line."""
    for raw in text.replace("\r", "\n").split("\n"):
        m = WAVE_LINE.search(raw)
        if not m:
            continue
        start = m.start()
        if start and re.match(r"[0-9A-F]", raw[start - 1]):
            continue
        h = [int(x, 16) for x in m.group(2).split()]
        if len(h) != 8:
            continue
        if half_order == "hi_lo":
            words = [(h[i] << 16) | h[i + 1] for i in range(0, 8, 2)]
        else:
            words = [(h[i + 1] << 16) | h[i] for i in range(0, 8, 2)]
        yield int(m.group(1), 16), words


def assemble_mem_page(text: str, start: int):
    """Return 256 bytes if the 16 expected lines are all present, else None."""
    got = {}
    for a, d in parse_mem_lines(text):
        got[a] = d
    out = bytearray()
    for i in range(16):
        a = (start + 16 * i) & 0xFFFFFFFF
        if a not in got:
            return None
        out += got[a]
    return bytes(out)


def assemble_wave_page(text: str, start_word: int, half_order="lo_hi"):
    """Return 128 words (list of u32) if all 32 expected lines are present."""
    got = {}
    for a, w in parse_wave_lines(text, half_order):
        got[a] = w
    out = []
    for i in range(32):
        a = start_word + 4 * i
        if a not in got:
            return None
        out += got[a]
    return out


# --------------------------------------------------------------- transport
class SerialLink:
    def __init__(self, port, baud):
        import serial  # pip install pyserial
        self.s = serial.Serial(port, baudrate=baud, bytesize=8, parity="N",
                               stopbits=1, timeout=0.05, xonxoff=False,
                               rtscts=False, dsrdtr=False)

    def write(self, b: bytes):
        self.s.write(b)
        self.s.flush()

    def read(self) -> bytes:
        n = self.s.in_waiting
        return self.s.read(n if n else 1)

    def reset_input(self):
        self.s.reset_input_buffer()

    def close(self):
        self.s.close()


class Console:
    def __init__(self, link, char_delay=0.003, prompt_re=DEFAULT_PROMPT_RE,
                 raw_log=None, verbose=False, quiet=0.3):
        self.link = link
        self.char_delay = char_delay
        self.prompt_re = re.compile(prompt_re)
        self.buf = ""
        self.raw_log = raw_log          # open text file or None
        self.verbose = verbose
        self.prompt_misses = 0
        self.use_prompt = True
        self.quiet = quiet

    # -- output side
    def send(self, s: str):
        for ch in s:
            self.link.write(ch.encode("ascii"))
            if self.char_delay:
                time.sleep(self.char_delay)

    def _pull(self):
        b = self.link.read()
        if not b:
            return False
        t = b.decode("latin-1")
        self.buf += t.replace("\r", "")
        if self.raw_log:
            self.raw_log.write(t)
        if self.verbose:
            sys.stdout.write(t)
            sys.stdout.flush()
        return True

    def expect(self, needle, timeout=5.0):
        """Wait for a literal string (or compiled regex).  Returns the text
        up to and including the match; raises TimeoutError otherwise."""
        end = time.time() + timeout
        while True:
            if isinstance(needle, str):
                i = self.buf.find(needle)
                if i >= 0:
                    j = i + len(needle)
                    out, self.buf = self.buf[:j], self.buf[j:]
                    return out
            else:
                m = needle.search(self.buf)
                if m:
                    out, self.buf = self.buf[:m.end()], self.buf[m.end():]
                    return out
            if not self._pull() and time.time() > end:
                raise TimeoutError("waiting for %r; got tail %r" %
                                   (needle if isinstance(needle, str)
                                    else needle.pattern, self.buf[-120:]))

    def soft_prompt(self, timeout=0.5):
        """Wait briefly for the idle prompt; stop waiting if it never shows."""
        if not self.use_prompt:
            return
        try:
            self.expect(self.prompt_re, timeout)
            self.prompt_misses = 0
        except TimeoutError:
            self.prompt_misses += 1
            if self.prompt_misses >= 3:
                self.use_prompt = False

    def drain(self, quiet=0.2, limit=3.0):
        end_q = time.time() + quiet
        end = time.time() + limit
        while time.time() < end_q and time.time() < end:
            if self._pull():
                end_q = time.time() + quiet
        self.buf = ""

    def resync(self):
        """Get back to an idle prompt (abandon any half-finished command)."""
        self.send("n\r")        # answers a pending More? prompt, harmless otherwise
        self.drain(self.quiet)
        self.send("\r")
        self.drain(self.quiet)

    # -- commands
    def mem_session(self, addr, cmd="db"):
        self.buf = ""
        self.send(cmd + "\r")
        self.expect(ADDR_PROMPT, 5)
        self.send("%08X\r" % addr)

    def _scan_mem_page(self, addr):
        """Look for one complete, valid db page at the start of the buffer.
        Returns bytes, or None if more input is needed.  Raises PageError when the
        real More? prompt (which sits at the start of a line -- the same text inside
        a data line's ASCII column does not count) shows up before 16 valid lines."""
        buf = self.buf
        pos = 0
        data = bytearray()
        while len(data) < C_PAGE:
            nl = buf.find("\n", pos)
            if nl < 0:
                m = MORE_AT_LINE_START.match(buf[pos:])
                if m:                     # prompt has no newline after it
                    self.buf = buf[pos + m.end():]
                    raise PageError("More? prompt after only %d valid bytes" % len(data))
                return None
            line = buf[pos:nl]
            m = MORE_AT_LINE_START.match(line)
            if m:
                self.buf = buf[pos + m.end():]
                raise PageError("More? prompt after only %d valid bytes" % len(data))
            mm = MEM_LINE.match(line)
            if mm and int(mm.group(1), 16) == (addr + len(data)) & 0xFFFFFFFF:
                try:
                    d = bytes(int(x, 16) for x in mm.group(2).split())
                except ValueError:
                    d = b""
                if len(d) == 16 and ascii_ok(d, mm.group(3)):
                    data += d
            pos = nl + 1
        # all 256 bytes are in; now consume up to the real prompt
        m = MORE_AT_LINE_START.search(buf, pos)
        if not m:
            return None
        self.buf = buf[m.end():]
        return bytes(data)

    def mem_page(self, addr, timeout=10.0):
        end = time.time() + timeout
        while True:
            r = self._scan_mem_page(addr)
            if r is not None:
                return r
            if not self._pull() and time.time() > end:
                raise TimeoutError("page 0x%08X incomplete; tail %r" % (addr, self.buf[-120:]))

    def wave_page_text(self, word_addr, cmd="wd", prompt=WAVE_PROMPT,
                       timeout=10.0, half_order="lo_hi"):
        self.send(cmd + "\r")
        self.expect(prompt, 5)
        self.send("%08X\r" % word_addr)
        # collect until all 32 lines are present
        end = time.time() + timeout
        while True:
            page = assemble_wave_page(self.buf, word_addr, half_order)
            if page is not None:
                text, self.buf = self.buf, ""
                return text
            if not self._pull():
                if self.prompt_re.search(self.buf):
                    raise PageError("wave page %08X: idle prompt before 32 valid lines" % word_addr)
                if time.time() > end:
                    raise TimeoutError("wave page %08X incomplete; tail %r" %
                                       (word_addr, self.buf[-120:]))
