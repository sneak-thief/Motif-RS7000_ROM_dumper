# ROM dumper for the Yamaha RS7000, Motif ES and Motif

Reads the program and wave ROMs of your own unit over a serial cable via the OS's built-in debug console and
turns the result into one image file per chip. 

It covers the RS7000, the Motif ES (6/7/8) and the original
Motif (6/7/8). The Motif ES Rack is not supported.


| Unit | Status |
|---|---|
| **RS7000** | **Verified on a real unit**: full program flash, style ROM and wave ROM dumps. |
| Motif ES | Not yet run on a unit. Addresses and formats come from the OS 1.08 image, the wiring from the schematic. |
| Motif | Not yet run on a unit. Addresses come from the OS 1.90 image and a schematic. |

The Motif ES and Motif support has only been checked against a simulated console (`test_sim.py`). Each section
below lists what is assumed.

**Read-only.** The tools only ever send `db` (read memory), `wd` (read wave ROM), `?`, `y`, `n` and Enter. They
never send the console's write commands (`cb`, `cw`, `cl`) or its reset (`rs`). Don't type into a terminal on this
port while the unit is wired up.

The dumps contain Yamaha's firmware and samples: keep them for your own use. 

**Don't bother asking me for rom dumps - they're only for my personal use. Please make your own instead.**


## What you need

- **Python 3.8 or newer** and `pip install pyserial`. On Windows, type `py` instead of `python3`.
- **A USB-to-serial adapter with 3.3 V logic levels** (FTDI FT232R/FT232H, CP2102, or a CH340 set to 3.3 V).
  - Only three wires are needed: TX, RX and GND, at 8N1 with no flow control.
  - **Not RS-232**: ±12 V destroys the CPU's pins. A 5 V TTL adapter is also unsafe.
  - Check with a meter that the adapter's TX idles at about 3.3 V.
- **Soldering:** thin wires to the board (see each unit's Hardware section). The unit runs on its own power supply,
  the adapter on USB, and they share a **common ground**.
- **Yamaha's OS updater file** for your unit's OS version (`.PGM`), to prove the program dump is correct.

**Baud rate:** 38,400. The firmware divides the CPU's peripheral clock by 512, which gives for example 39,062 at
20 MHz; 38,400 is within tolerance. `probe --scan` tries 38400, 39062, 31250, 57600, 19200, 9600 and 115200.

**Serial port** (`--port`; close any terminal program on it first):

| OS | Find it | Example |
|---|---|---|
| Windows | Device Manager > Ports (COM & LPT) | `--port COM3` (COM10 and up: `--port \\.\COM12`) |
| Linux | `ls /dev/ttyUSB*`; your user must be in the `dialout` group | `--port /dev/ttyUSB0` (the default) |
| macOS | `ls /dev/cu.usbserial*` | `--port /dev/cu.usbserial-A50285BI` |

## How it works

```
USB-serial adapter (3.3 V)  --  CPU serial port SCI0  --  OS debug console ("CNS >")

rs7k-motif_dumper.py        probe, mem, wave, wave-probe, verify   talks to the unit, writes dumps, resumable
rs7k-motif_dump-parser.py   check-update, carve, split-wave, ...   offline: checks the dumps, cuts them into chip files
```

The steps, in order:

| Step | Command | What it does |
|---|---|---|
| 1 | `probe --scan` | Finds the baud rate and shows the console menu. |
| 2 | `mem` | Dumps the program flash (and the RS7000's style ROM) with `db`. |
| 3 | `check-update` | Compares the program dump with Yamaha's updater file. A match proves the whole capture path. |
| 4 | `carve` | Cuts the dump into one file per chip, plus a merged program file. |
| 5 | `mem --area mask` | Motif only: dumps the program mask ROM IC101. |
| 6 | `wave` | Dumps the wave ROM through the sound chip with `wd`. This takes hours (days on the Motif ES). |
| 7 | `verify` | Reads the unit again and compares with the dump: the only check for single flipped digits. |

**Resuming:** every dump continues where it stopped after an interrupt (Ctrl-C, a pulled cable): run the same
command again. Bad pages are retried automatically.

**Options for all dumper commands:**

| Option | Default | Meaning |
|---|---|---|
| `--target rs7000\|es\|motif` | `rs7000` | Sets the sizes, the wave half order and the output folder (`dump`, `dump_es`, `dump_motif`). |
| `--port`, `--baud` | `/dev/ttyUSB0`, 38400 | Use the same values for `verify` as for the dump. |
| `--raw-log FILE` | none | Keeps the whole console transcript. `mem-log` / `wave-log` can rebuild a dump from it. |
| `--timeout` | 10 s | How long to wait for a page. |
| `--char-delay` | 0.003 s | Pause between typed characters; raise it if the unit drops characters. |
| `--max-retries` | 5 | Retries per page. |

`mem` and `wave` also take `--out DIR` (output folder) and `--restart` (ignore saved progress). The parser's `carve`
and `split-wave` write to `dump/chips/` for every unit unless you give `--out`.

---

## RS7000 (`--target rs7000`, the default): verified on hardware. Used to build the RS7007 emulator.

### Hardware

Use header **CN23** (not fitted at the factory). It has 3.3 V logic:

| CN23 pin | Signal | Connect to |
|---|---|---|
| 1 | +5 V | **nothing** |
| 2 | TXD0 (unit sends) | adapter RX |
| 3 | RXD0 (unit receives) | adapter TX |
| 4 | GND | adapter GND |

R148 (pin 2) and R149 (pin 3) are not fitted. Bridge or fit them, or solder to their CPU-side pads.

### Commands

```
python3 rs7k-motif_dumper.py probe --port COM3 --scan
python3 rs7k-motif_dumper.py mem --port COM3 --length 0x1000                      # 16 pages: look at them first
python3 rs7k-motif_dumper.py mem --port COM3 --raw-log dump/mem.log               # all 6 MB, 2-3 h
python3 rs7k-motif_dump-parser.py check-update dump/cs0_flash_00000000.bin 0532OS_.PGM
python3 rs7k-motif_dump-parser.py carve dump/cs0_flash_00000000.bin               # -> dump/chips/
python3 rs7k-motif_dumper.py wave --port COM3 --words 0x800                       # look at it first
python3 rs7k-motif_dumper.py wave --port COM3                                     # all 16 MB, 5-6 h
python3 rs7k-motif_dumper.py verify wave dump/wave_words_lo_hi.bin --samples 0 --port COM3
python3 rs7k-motif_dump-parser.py split-wave dump/wave_words_lo_hi.bin            # -> dump/chips/
```

### Files

| File | Contents | Size | Made by |
|---|---|---|---|
| `cs0_flash_00000000.bin` | IC37 + IC79 + IC93 as the CPU sees them (CPU address 0) | 6 MB | `mem` |
| `ic37_program1.bin` | program flash 1 (CPU 0x000000) | 2 MB | `carve` |
| `ic79_program2.bin` | program flash 2 (CPU 0x200000) | 2 MB | `carve` |
| **`rs7000_program_merged.bin`** | **IC37 + IC79 merged** | 4 MB | `carve` |
| `ic93_style.bin` | style mask ROM (CPU 0x400000) | 2 MB | `carve` |
| `wave_words_lo_hi.bin` | wave ROMs IC65 + IC68 as 32-bit words | 16 MB | `wave` |
| `ic65_wave_l.bin`, `ic68_wave_h.bin` | the wave ROMs' low and high 16-bit halves | 8 MB each | `split-wave` |

### Notes

- **`check-update`** with `0532OS_.PGM`: bytes 0x20000-0x3946C5 must read IDENTICAL on a unit running OS V1.22.
  Another OS version needs its own updater; the dump has the version string at 0x20000.
- **`carve`** checks IC93 the way the OS does (the 16-bit word sum must be 0xAA55). If that fails it exits with
  code 1: read IC93 again and compare.
- **IC93 on its own** (about 50 min): `mem --start 0x400000 --length 0x200000`, then
  `carve dump/cs0_flash_00400000.bin`. The start address comes from the file name (or `--start`). A partial dump only
  gives the chips that are completely inside it, and no merged file.
- **Wave:** the default is 16 MB (`--words 0x400000`, in 32-bit words). The half order `lo_hi` (the first printed
  half-word is the low half), the chip assignment and the file layout were confirmed on a real unit.
- **`verify wave ... --samples 0`** reads every page a second time, so it takes as long as the dump. To continue an
  interrupted run, add `--start-page N`. A dump made with `--half-order hi_lo` is named `wave_words_hi_lo.bin` and
  needs the same option.

---

## Motif ES (`--target es`): not yet run on a unit

### Hardware

There is no console header, but the ES schematic shows two unpopulated parts on the CPU's SCI0 pins, which give you
pads to solder to:

| CPU pin | Signal | Solder point | Connect to |
|---|---|---|---|
| 198 | RxD0/SCPT[0] (unit receives) | **IC42 pin 5** (IC42 not fitted) | adapter TX |
| 191 | TxD0/SCPT[0] (unit sends) | **R243**, the pad on the CPU pin 191 side (R243 not fitted) | adapter RX |
| | GND | any ground | adapter GND |

Solder three wires with the unit switched off. Check with the meter that each pad has continuity to its CPU pin
before soldering. Then run `probe --target es --scan`.

The ES console uses divisor 26 (baud = peripheral clock / 864), so it only runs at 38,400 if that clock is about
33.18 MHz. The CPU clock isn't known yet, which is why `--scan` matters here.

### Commands

```
python3 rs7k-motif_dumper.py probe --target es --port COM3 --scan
python3 rs7k-motif_dumper.py mem --target es --port COM3 --start 0x20000 --length 0x100      # must show 1.08
python3 rs7k-motif_dumper.py mem --target es --port COM3                                      # 4 MB, about 1.4 h
python3 rs7k-motif_dump-parser.py check-update --target es dump_es/cs0_flash_00000000.bin 0745OS_.PGM
python3 rs7k-motif_dump-parser.py carve --target es dump_es/cs0_flash_00000000.bin           # -> dump/chips/
python3 rs7k-motif_dumper.py wave --target es --port COM3 --words 0x800                       # look at it first
python3 rs7k-motif_dumper.py wave --target es --port COM3                                     # 96 MB, 22-25 h
python3 rs7k-motif_dumper.py verify wave dump_es/wave_words_hi_lo.bin --target es --samples 0 --port COM3
python3 rs7k-motif_dump-parser.py split-wave --target es dump_es/wave_words_hi_lo.bin         # -> dump/chips/
```

### Files

| File | Contents | Size | Made by |
|---|---|---|---|
| `cs0_flash_00000000.bin` | program flash as the CPU sees it | 4 MB (assumed) | `mem` |
| `es_program.bin` | the same, carved; the chip layout is unknown, so there are no per-chip files | 4 MB | `carve` |
| `wave_words_hi_lo.bin` | all wave ROMs as 32-bit words | 96 MB (assumed) | `wave` |
| `es_wave_lo16.bin`, `es_wave_hi16.bin` | the low and high 16-bit halves (not individual chips) | 48 MB each | `split-wave` |

### Notes (from the OS 1.08 image, `0745OS_.PGM`)

- The `db` and `wd` text formats are the RS7000's.
- **`check-update --target es`** must match the updater's program image (0x20000-0x3F5C01) exactly.
- **Half order:** `wd` prints the high half of each 32-bit word first, so the default is `hi_lo`. This is inferred
  from the code; if it's wrong, `split-wave --swap` fixes it.
- If `wd` prints stale or all-zero pages, look first at the RAM flag at 0x8C2FF890: the ES skips the read when it is set.
- **Assumed, not in the firmware:** the 96 MB wave ROM size (`--words` overrides it) and the 4 MB program flash at
  0xA0000000 (`--length` overrides it).

---

## Motif 6/7/8, original (`--target motif`): not yet run on a unit

### Hardware

There is no console header. The console is the CPU's SCI0: **TxD0 is CPU pin 164, RxD0 is CPU pin 171** (SH-3,
208-pin QFP; pins 157-208 are side 4, so these are the 15th and 8th pins from the 156/157 corner). Neither pin goes
to a connector or test point; each only has a 10 k resistor to a supply rail, which you can solder to:

| Adapter | Unit |
|---|---|
| TX | RxD0: the CPU-side pad of **R46** (or CPU pin 171) |
| RX | TxD0: the CPU-side pad of **R390** (or CPU pin 164) |
| GND | any ground |

Work with the unit switched off. Find the CPU-side pad of each resistor with the meter (continuity to the CPU pin)
before soldering, and leave the resistors in place. The fallback is fine enamelled wire on the CPU pins. The rear
TO HOST and MIDI connectors are not this port.

### Commands

```
python3 rs7k-motif_dumper.py probe --target motif --port COM3 --scan                          # look for the CONSOLE FUNCTIONS menu
python3 rs7k-motif_dumper.py mem --target motif --port COM3 --start 0x20000 --length 0x100    # first line: A0020000 31 2E 39 30 ... 1.90
python3 rs7k-motif_dumper.py mem --target motif --port COM3 --raw-log dump_motif/mem.log      # program flash, 4 MB, about 1.5 h
python3 rs7k-motif_dump-parser.py check-update --target motif dump_motif/cs0_flash_00000000.bin 0638OS_.PGM
python3 rs7k-motif_dump-parser.py carve --target motif dump_motif/cs0_flash_00000000.bin      # -> dump/chips/
python3 rs7k-motif_dumper.py mem --target motif --area mask --port COM3                        # IC101, 2 MB, about 45 min
python3 rs7k-motif_dump-parser.py carve --target motif dump_motif/mask_rom_00000000.bin        # -> ic101_mask.bin, signature checked
python3 rs7k-motif_dumper.py wave-probe --target motif --port COM3                             # where does the wave ROM end?
python3 rs7k-motif_dumper.py wave --target motif --port COM3 --words 0x800                     # look at it first
python3 rs7k-motif_dumper.py wave --target motif --port COM3 --words N                         # full run, about 14 min per MB
python3 rs7k-motif_dumper.py verify wave dump_motif/wave_words_lo_hi.bin --target motif --samples 0 --port COM3
python3 rs7k-motif_dump-parser.py analyze-wave dump_motif/wave_words_lo_hi.bin                 # check the half order
```

### Files

| File | Contents | Size | Made by |
|---|---|---|---|
| `cs0_flash_00000000.bin` | IC31 + IC25 as the CPU sees them (CPU address 0) | 4 MB | `mem` |
| `ic31_program.bin` | program flash IC31: the even 16-bit halves | 2 MB | `carve` |
| `ic25_program.bin` | program flash IC25: the odd 16-bit halves | 2 MB | `carve` |
| **`motif_program_merged.bin`** | **IC31 + IC25 merged**: the CPU's 4 MB view, including the 128 KB boot kernel | 4 MB | `carve` |
| `mask_rom_00000000.bin` | program mask ROM IC101 (CPU 0x10000000) | 2 MB | `mem --area mask` |
| `ic101_mask.bin` | IC101, signature checked | 2 MB | `carve` |
| `wave_words_lo_hi.bin` | wave ROMs IC70, 71, 73, 74, 78, 79 as 32-bit words | unknown | `wave --words N` |
| `motif_wave_lo16.bin`, `motif_wave_hi16.bin` | the low and high 16-bit halves (not individual chips) | half each | `split-wave` |

### Notes

- **Interleaved program flash:** IC31 and IC25 share a 32-bit bus (the OS's flash table is {0xA0000000, 0xA0000002}),
  so the 4 MB image is not "IC31, then IC25". `ic31_program.bin` is bytes 0-1 of every 4 bytes and `ic25_program.bin`
  is bytes 2-3. Which chip is which is assumed from the table order; `carve --swap-chips` swaps them.
- **`check-update`** with `0638OS_.PGM`: bytes 0x20000-0x3FEABB must read IDENTICAL on a unit running OS V1.90. The
  updater doesn't contain the first 128 KB (the boot kernel), so that part is only checked by dumping it twice or
  running `verify mem`.
- **IC101:** 2 MB at CPU address 0x10000000 (chip select 4), read at 0xB0000000. `carve` checks it the way the OS does
  (16-bit word sum 0xAA55, the same test as the RS7000's IC93) and exits with code 1 if it fails: read it again. To
  check it on the unit: `verify mem dump_motif/mask_rom_00000000.bin --base 0xB0000000 --samples 40`.
- **The wave ROM size isn't in the firmware**, so `wave` refuses to run without `--words N` (N = total bytes of the six
  chips / 4; six 128 Mbit chips = 96 MB = `0x1800000`). Read the chip part numbers, or run `wave-probe`: it reads one
  page at word 0 and at 2^16 ... 2^25 and reports each as `data`, `SAME AS PAGE 0` (the address wrapped),
  `all FFFFFFFF` or `all 0`. The ROM ends where the real data ends.
- **Wave chips:** how IC70 ... IC79 map onto the wave addresses is unknown, so there are no per-chip wave files. The
  half order `lo_hi` is inferred from the code (check it with `analyze-wave`; `split-wave --swap` fixes it).
- **Don't send `e`** on the Motif's console: it changes how `wd` reads (RAM flag 0x8C384998).

**Assumed, not verified:** that the CPU pins are set up as the serial port (the OS never sets them; the boot kernel
might), the real baud rate (the CPU clock is unknown), IC31 = even halves / IC25 = odd halves, the wave ROM size,
chip mapping and half order, and the console formats (read from the code, not seen on a unit). If `probe` or the
first `mem` shows something different, keep the `--raw-log` and look at it.

What the V1.90 firmware says (flash addresses; the OS runs from RAM = flash + 0x8BFF0000):

| Item | Value | Where |
|---|---|---|
| Console port | SCI0 (0xFFFFFE80), 8N1, SCBRR 15 (baud = Pphi/512); starts at boot with the prompt `CNS >` | TX 0x24488, init 0x243CA-0x243E8 |
| Commands | `?`, `db`/`dw`/`dl`, `wd`/`wn`/`wds`/`wns`, `wl`, `cb`/`cw`/`cl`, `cs`, `rs`, ... as on the RS7000 | menu 0x1B6BB0 |
| `wd` read | the RS7000's protocol at base 0xB4000000 (RS7000: 0xB80848xx); each dword stored [reg 0x29E, reg 0x29C], so `lo_hi` | 0x321B24 |
| Program flash | 4 MB window at 0xA0000000; two interleaved chips at 0xA0000000 and 0xA0000002 | address check 0x98A30, table 0x1BB948 |
| IC101 | 2 MB at 0x10000000, sum check 0xAA55 | 0x419FE |
| Test screen | lists "Flash ROM IC-31, IC-25, IC-36" and "Wave ROM IC.-1 ... IC.-6"; the third flash entry is probably the mask ROM test, labelled IC-36 instead of IC101 | 0x1B901C |

---

## Troubleshooting

- **`probe` shows no menu:** check that TX and RX aren't swapped (adapter TX goes to the unit's RxD0), the ground,
  the 3.3 V levels, and that the unit has finished booting. Try `--scan`.
- **Garbage or many retries:** wrong baud rate (`--scan`), or the unit drops characters (`--char-delay 0.01`).
- **`check-update` shows differing 4 KB blocks:** either the unit runs another OS version (see the version string
  at 0x20000) or the capture went wrong (look at the pattern of the blocks). Dump again.
- **Undetected errors:** memory pages carry an ASCII column that is cross-checked with the hex bytes, but a flipped
  digit in a non-printable byte isn't caught: use `check-update` and `verify`. Wave pages have no redundancy at all:
  always run `verify wave ... --samples 0`, or dump twice and compare.

**Exit codes:** 0 = OK; 1 = signature failed or bad argument; 2 = `--words` missing or wrong input file; 3 = gave up
after retries (progress saved, run again to resume); 4 = dump incomplete; 5 = `verify` found differences or
unreadable pages.

## Files in this repository

| File | Purpose |
|---|---|
| `rs7k-motif_dumper.py` | talks to the unit: `probe`, `mem`, `wave`, `wave-probe`, `verify` |
| `rs7k-motif_dump-parser.py` | offline: `check-update`, `carve`, `split-wave`, `analyze-wave`, `mem-log`, `wave-log` |
| `rs7k-motif_cns.py` | console protocol and per-unit settings, shared by both |
| `test_sim.py` | self-test against a simulated console |
| `CHANGELOG.md` | changes |
| `LICENSE` | MIT license |

## Self-test

`test_sim.py` drives the real dumper and parser code against a simulated console built from the firmware's text
formats. It needs no hardware and no pyserial, but it does need an RS7000 program flash image (4 MB, IC37 + IC79,
e.g. your own `rs7000_program_merged.bin`):

```
FLASH_BIN=dump/chips/rs7000_program_merged.bin python3 test_sim.py        # ends with ALL SIMULATION TESTS PASSED
```

Optional updater comparisons: `PGM=0532OS_.PGM` (RS7000), `ES_FLASH_BIN=... ES_PGM=0745OS_.PGM` (Motif ES),
`MOTIF_PGM=0638OS_.PGM` (Motif). On Windows, set the variables with `set NAME=value` first.

## License

The code is under the [MIT license](LICENSE). It covers only these tools: the ROM dumps you make with them contain
Yamaha's copyrighted firmware and samples, which no license here lets you share.
