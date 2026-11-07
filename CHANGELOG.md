# Changelog

### 1.8 (2026-11-07)
- Renamed: `rs7kdump-1-dumper.py` -> `rs7k-motif_dumper.py`, `rs7kdump-2-dump-parser.py` -> `rs7k-motif_dump-parser.py`,
  `rs7kcns.py` -> `rs7k-motif_cns.py`.  The shared module's name has a hyphen, so the scripts load it by path
  (one copy, shared by all three) instead of `import`.  No other code change.
- MIT license (`LICENSE`, SPDX lines in the scripts).

### 1.7 (2026-11-07)
- README rewritten for clarity: status table, what you need, steps, options, then per unit: hardware, commands,
  files, notes.  Corrected: `--out` / `--restart` belong to `mem` and `wave` only; `carve` / `split-wave` write to
  `dump/chips/` for every unit; the self-test needs an RS7000 flash image (`FLASH_BIN`); the Motif ES wiring
  reference; exit code 3.  Rack units named as not supported.
- Motif ES wiring from the schematic: RxD0 (CPU pin 198) at IC42 pin 5, TxD0 (CPU pin 191) at the CPU-side pad of
  R243 (both not fitted), plus GND.
- `rs7kcns.py`: docstring no longer says nothing was tested on hardware.
- `.gitignore`: keeps dumps, logs, updater files and ROM images out of the repository.  No code change.

### 1.6 (2026-11-07)
- RS7000 flow confirmed on a real unit by the owner (full program flash, style ROM and wave dump).  README status updated; no code change.

### 1.5 (2026-11-07)
- README rewritten: one intro, then per synth: files, hardware, commands, details.
- Merged program files: `carve` now also writes `rs7000_program_merged.bin` (IC37 + IC79) and, for the Motif, `motif_program_merged.bin`.
- Motif: program flash IC31 / IC25 are interleaved on a 32-bit bus (the OS's flash table is {0xA0000000, 0xA0000002}; the RS7000's is
  {0xA0000000, 0xA0200000}), so `carve --target motif` de-interleaves them into `ic31_program.bin` / `ic25_program.bin`
  (`--swap-chips`, `--chip-endian`).  Program mask ROM IC101 (2 MB at 0x10000000, the OS checks it with the same 0xAA55 sum as the RS7000's
  IC93): `mem --target motif --area mask` dumps it, `carve --target motif` writes `ic101_mask.bin` after the signature check.
- `test_sim.py`: merged files, lanes, mask ROM dump + signature (good and corrupted).

### 1.4 (2026-11-07)
- `--target motif`: original Motif 6/7/8 (V1.90 `WSSx` OS).  Console = SCI0, SCBRR 15 (baud = Pphi/512), same formats as the
  RS7000; wave read = RS7000 protocol at base 0xB4000000 (`lo_hi`); flash window 4 MB at 0xA0000000.  `check-update` reads the
  `WSSx OS File`, `carve` writes `motif_program.bin`, `split-wave` writes `motif_wave_lo16.bin` / `motif_wave_hi16.bin`.
- Wave ROM size has no default for the Motif: `wave` needs `--words` (exit code 2 otherwise).
- New `wave-probe`: one `wd` page at word 0 and at 2^k, reported as data / same as page 0 / all FFFFFFFF / all 0.
- `probe` prints Motif-specific wiring help.  `test_sim.py`: Motif cases (4 MB mem dump, updater comparison against the real
  `0638OS_.PGM` when `MOTIF_PGM=` is set, `wave` refusal without `--words`, `wave-probe`).  No change for rs7000 / es.

### 1.3 (2026-10-14)
- Folders are created on demand: `--raw-log wave/wave.log` (or any path) no longer crashes with `FileNotFoundError` when the folder does not exist yet; `--out` already did this.
- `verify` is now as robust as `wave` / `mem`: it resyncs the console at the start, retries a page that times out or
  fails to parse (`--max-retries`), re-reads a page that differs from the file before counting it as a mismatch, prints
  progress / ETA, reports `UNREADABLE` pages, and has `--start-page N` to continue an interrupted run.  Before, the first
  garbled or timed-out page ended the whole run with a Python traceback (`PageError` / `TimeoutError`) - on a 32 768-page
  `--samples 0` run that is almost certain on real hardware.  Exit code 0 = identical, 5 = mismatches or unreadable pages.
- `test_sim.py`: new verify cases (clean, noisy, `--start-page`, a flipped digit must be flagged, `verify mem`).

### 1.2 (2026-10-11)
- `--target es` (Motif ES): defaults, `check-update` for the `MOTIF2 OS File` updater, `carve`/`split-wave` ES names, simulator ES cases (hi-first wave order).  No change for rs7000.

### 1.1 (2026-10-07) - verified against the v1.22 OS image
Checked by tracing the console code in the OS image and re-running the simulator with the firmware's
real output layout.  Still not tested on hardware.

Changed
- Wave half order: the default `--half-order` is now `lo_hi` (was `hi_lo`).  The OS read routine
  (ROM 0x1955CC) stores each dword as [SWP reg 0x29E, reg 0x29C] and SWP30 register pairs put the
  high half at the lower offset, so the first printed half-word is most likely the low half.  Inferred,
  not measured; a wrong guess is recoverable with `split-wave --swap`.
- Wave dumps are now written to `wave_words_lo_hi.bin` (was `wave_words_hi_lo.bin`).
- `carve` accepts a partial dump such as `cs0_flash_00400000.bin` (IC93 only).  The start address is
  taken from the file name, or from the new `--start` option.  Chips that are not fully inside the
  file are skipped.
- `mem` now sends `n` + CR before starting, so a "More?" prompt left over from an interrupted run no
  longer breaks a resumed dump.
- README: baud wording corrected (baud = peripheral clock / 512; the clock is not in the firmware),
  IC93-only steps added, file names updated.

Added
- `carve` checks IC93 against the OS's own self-test: the 16-bit sum of the big-endian words at
  0x400000 + 2^k (k = 1..20) and at 0x5FFFFE must equal 0xAA55.  Prints OK or FAILED; exit code 1 on failure.
- `mem` retries a failed `db` session start instead of ending with a traceback.
- `test_sim.py`: the simulated console now matches the firmware (`CNS >` prompt, 60 dashes, no blank
  line before "More?", low-half-first wave lines).  New cases: IC93-only dump and carve, a corrupted
  IC93 failing the signature, resume after a stale "More?".  `PGM=` and `FLASH_BIN=` select the
  updater file and flash image.

Verified against the firmware (no code change needed)
- `db`: prompt, line format, 256-byte pages, Y/N continuation, ASCII column 0x20-0x7E.
- `wd`: 8 half-words per line, address +4 dwords per line, 256 half-words per page, no "More?" prompt.
- Idle prompt `CNS >` / `CNS*>` matches `DEFAULT_PROMPT_RE`.  There is no `n` or `y` command, so the
  resync keystrokes are harmless.  Line input ends on CR.
- `check-update` segments (0x20000-0x1FFFFF and 0x200000-0x3946C5) match the Yamaha PGM.

Known limits (unchanged)
- Wave lines have no per-line error check: run the dump twice and compare, or use `verify wave ... --samples 0`.
- Baud rate and the SCI0 <-> CN23 channel are unproven on hardware.
- Wave chip assignment (IC65 = low, IC68 = high) and chip byte order are assumptions until compared
  with a T76 read.

### 1.0
Initial version as received: `probe`, `mem`, `wave`, `verify`, parser tools and simulator self-test.

