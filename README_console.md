# Yamaha RS7000 firmware: the built-in text console

The OS has a small command console (module tag `[CNS]`, banner `CONSOLE FUNCTIONS`). This file lists
every command, what it prompts for, what it prints and where its code is. It was worked out by reading the
firmware (OS v1.22 image, `flash4m.bin`), and is the basis of the dumper in this repository
(README.md).

**Since confirmed on a real RS7000:** the console is live on a production unit with nothing to enable. It is
reached through the CPU's SCI0 on header CN23 (README.md, "RS7000: Hardware"), runs at 38,400 baud, and `db`
and `wd` behave as described in §6 and §7: the dumper read the whole program flash, the style ROM and the wave ROM
with them. The other commands have still only been read from the code.

Evidence tags: **[HW]** seen on a real unit; **[FW]** read from the code or strings; **[INF]** inferred from code
or register values; **[?]** not traced.

Address convention: ROM address = runtime address − `0x8C000000` + `0x10000` (the 4 MB flash offset of the
code); data pointers `0x80xxxxxx` = flash offset `xxxxxx`.

## 1. Basics

- On start the console prints `[CNS] cns start!`, then loops printing a `>` prompt and reading a line.
  The line is compared with each command name in turn using one string-compare routine; names are
  matched whole (`wd` is not `wds`) and are lower case. **[FW]**
- `?` prints a boxed help screen (`CONSOLE FUNCTIONS`, "Last update: Sep 28 2004" in the RS7000 image).
  The command table is in the dispatcher around ROM `0x26490`-`0x268E4`; the strings are at flash
  `0x32F644`-`0x32F71C` and the help text from `0x32F71C`. **[FW]**
- **Only the commands take arguments from prompts.** You type the command, then answer the questions it
  asks. Hex numbers are read with `%x` (no `0x` prefix needed); task IDs, priorities, waveform and keybank
  numbers: the task commands read decimal `%d`, the wave commands read hex `%x`. **[FW]**
- **Output gating.** All console output goes through `cns_printf` (ROM `0x26024`). Output from other tasks
  is dropped unless the enable flag (RAM `0x8C1B20F8`, set by `e`) or the force flag (`0x8C1B20FC`, set
  around `b`) is on; a call from task 32 is always printed (presumably the console task) **[INF]**.

## 2. Command index

| Command | Menu text | Handler (ROM) | Group |
|---|---|---|---|
| `e` | Enable cns_printf() | inline in dispatcher | I/O |
| `d` | Disable cns_printf() (default) | inline | I/O |
| `sm` | SYSMT mode | `0x26C90` | debug |
| `b` | View Trace Buffer | `0x26D3E` | debug |
| `to` | View Task Occupancy | `0x270B4` | debug |
| `t` | Reference Task | `0x27270` | debug |
| `s` | Reference Semaphore | `0x275EE` | debug |
| `f` | Reference Event Flag | `0x27702` | debug |
| `p` | Reference Memory Pool | `0x2784C` | debug |
| `c` | Reference Configuration | `0x2797E` | debug |
| `cp` | Change Task Priority | `0x27ACC` | debug |
| `st` | Suspend Task | `0x27B80` | debug |
| `rt` | Resume Task | `0x27BD6` | debug |
| `rr` | Rotate Ready Queue | `0x27C2C` | debug |
| `m` | Call MyDebugger() | `0x4DF64` | local |
| `db` `dw` `dl` | Dump (byte / word / long) | `0x27F8C` / `0x2815C` / `0x2832C` | misc |
| `cb` `cw` `cl` | Change (byte / word / long) | `0x284A4` / `0x28590` / `0x2863C` | misc |
| `vs` | View SCSI Inquiry | `0x27C82` | misc |
| `wl` | Waveform List | `0x28AA4` | misc |
| `wm` | Wave Memory List | `0x28AF6` | misc |
| `wd` | Wave Dump | `0x27E3E` | misc |
| `wn` | Wave Dump via WF&KB number | `0x27EB4` | misc |
| `wds` | Wave Dump (SSI) | `0x27E3E` (flag set) | misc |
| `wns` | Wave Dump via WF&KB number (SSI) | `0x27EB4` (flag set) | misc |
| `fd` | SEQ FAT Dump | `0x28D0C` | misc |
| `sf` | Search illegal FAT | `0x28E86` | misc |
| `pi` | Profiler initialize | `0x28714` | misc |
| `pv` | Profiler view | `0x2881C` | misc |
| `cs` | Program area check sum | `0x28A2E` | misc |
| `rs` | Reset | `0x28912` | misc |
| `?` | Help | `0x268E8` | misc |

The task-debug set (`b` to `rr`, `sm`) is a µITRON-style kernel inspector; the failure strings name the
kernel calls: `ref_cfg`, `ref_tsk`, `ref_sem`, `ref_flg`, `ref_mpl`, `chg_pri`, `sus_tsk`, `rsm_tsk`,
`rot_rdq`, `get_tid`, `def_int`. **[FW]**

## 3. Standard I/O

- **`e`**: sets the enable flag; prints `printf() enable!`.
- **`d`**: clears it; prints `printf() disable!`. This is the power-on default.

## 4. Debug: kernel and task inspection

All of these only read kernel state except `cp`, `st`, `rt`, `rr` (which change it).

- **`sm`**: the system monitor. First prompt: `"RAM_C_hisysmt" address? (@himixb.map) (default=0x…)`,
  the address of the SYSMT structure (the name refers to a linker map). Then a `SYSMT>` prompt with:
  `1` view all tasks' SP and PC (columns ID, SP, PC, NAME), `2` view all memory pools' used area
  (ID, USED-AREA `0x…-0x…`), `q` quit, `?` menu. The menu also shows `...to be continued...`.
- **`b`**: trace buffer viewer. Prints either `Trace buffer is not used.` or the buffer top address,
  bottom address, insert pointer, ring-buffer status and an "Executing" flag. Then asks
  `View task ID? (1-n, 0=all)` and `Without CONT entry? (Y/N)`, and lists entries as
  `ATTR ID LTIME EVENT PC`. ATTR is `task event`, `SVC event` or `I/O event`; EVENT is `SVC`, `RTN`,
  `CONT` or `IDLE`. `No trace entry.` if empty. It sets the force flag while printing.
- **`to`**: task occupancy. Reads the same trace buffer and prints `Result latest <n>msec.` followed by one
  `ID %` row per task (the share of time that task ran in that span) **[INF]**; `No trace entry.` if
  tracing is off.
- **`t`**: task list. Columns `ID PRI STAT WAIT WID WUPCNT SUSCNT PND REMSTKSZ NAME`. STAT is `RUN`, `RDY`,
  `WAI`, `SUS`, `WAS`, `DMT` (and `STK`, `???` for others). WAIT is `SLP`, `DLY`, `FLG`, `SEM`, `MBX`,
  `SMBF`, `MBF`, `MPL`, `MPF`, `TFL`, `PPL`; WID is the ID of the object waited on.
- **`s`**: semaphores. Columns `ID CNT OWN WTSK ATR NAME`; ATR is `FIFO` or `PRI`.
- **`f`**: event flags. Columns `ID WTSK PTN ATR NAME`; PTN is the flag pattern, ATR is `WSGL` or `WMUL`.
- **`p`**: variable-size memory pools. Columns `ID WTSK FREE MAX MIN TOTAL NAME`.
- **`c`**: kernel configuration: Kernel Mask Level, Time Interval, and the maximum number of tasks
  (and static tasks), task priorities, event flags, semaphores, mailboxes, message buffers, variable-
  and fixed-size memory pools, cyclic handlers, alarm handlers, I/O numbers and supervisor-call codes.
- **`cp`**: `Task ID? (1-n)`, shows `Current priority = …`, asks `New priority? (1-n)` and changes it
  (`chg_pri`).
- **`st`**: `Task ID? (1-n)`; suspends that task (`sus_tsk`).
- **`rt`**: `Task ID? (1-n)`; resumes it (`rsm_tsk`).
- **`rr`**: `Rotate Priority (1-n)`; rotates the ready queue at that priority (`rot_rdq`).

Entering a wrong ID shows the kernel's error code (`[CNS] chg_pri() failed. (err:%d)` etc.).

## 5. Local: `m` (MyDebugger)

A developer scratch debugger left in the image. `m` calls it (ROM `0x4DF64`); it shows a `MyDebugger>`
prompt and takes **one-letter** commands. The help text it prints (`?`) disagrees with the code for `1`
and `2`; the code is what runs. **[FW]**

| Key | Code does | Help text says |
|---|---|---|
| `?` | print help | help |
| `1` | clear bit 3 of a CPU port register (`0xA4000108`+32), print `PTE3=0` | wav load 1.WAV |
| `2` | set that bit, print `PTE3=1` | wav load 2.WAV |
| `3` `4` `5` | load `g:\3.WAV`, `g:\4.WAV`, `g:\5.WAV`, print the result | wav load 3/4/5.WAV |
| `t` | "test swp": a test routine at ROM `0x4E1BC` that prints `%.4X` values (not traced) [?] | test swp |
| `d` | print the address of `MyDebugger()` | same |
| `a` | raise a test assertion with the text `test` (`[CNS] Assertion occurred : "test"`) | Assertion |
| `i` | profiler init (same as `pi`) | Prof init |
| `p` | profiler mark test: marks 0-4 with kernel delays between, then shows the result | Prof mark test |
| `P` | profiler start/end test, then shows the result | Prof s-e test |
| `q` | quit | quit |

## 6. Misc: memory

All memory commands work on **raw addresses** with no range or alignment checks, so a bad address can
bus-error or hang the unit.

- **`db`, `dw`, `dl`**: dump memory (`db` **[HW]**). Prompt `Address ? : 0x`. Prints a header, then 256 bytes from that
  address: `db` 16 bytes per row, `dw` 8 words per row (`%.4X`), `dl` 4 longs per row (`%.8X`), each row
  starting with its address, plus an ASCII column for `db`/`dw`/`dl` (bytes `0x20`-`0x7E` as characters,
  others as blanks). Then `More? (Y/N) :`; `Y` continues with the next 256 bytes, `N` returns to `>`.
  The row address is rounded down to 16 even if the start was not aligned.
- **`cb`, `cw`, `cl`**: change memory. Prompt `Address ? (0x<last address>) : 0x`; the default is the
  address used last (kept at RAM `0x8C1B2104`). Then for each address it prints
  `0x<addr> (0x<current value>) : 0x` (2, 4 or 8 hex digits). Type a hex value to write it and advance by
  1, 2 or 4 bytes; type `.` to stop. After each write it reads the value back and prints `Verify error!`
  (and stops) if it differs, which is how it detects ROM or read-only addresses. The line is parsed
  with `%x` and the result is not checked, so a blank line is not a safe "skip". `cw`/`cl` need aligned
  addresses on this CPU.

## 7. Misc: waveform and sound memory

- **`wl`** (waveform list): prints `Waveform NumOfKeyBank`, then one row `<waveform> <keybanks>` for each
  waveform 0-255 that has at least one keybank.
- **`wm`** (wave memory list): prints `1block = 32kw` and a map of wave memory, 16 blocks per row (columns
  `0000`-`000F`), 1024 blocks of 32 kwords. A used block shows a 2-digit hex id (apparently the
  waveform number [INF]); a free block shows `__`.
- **`wd`** **[HW]**: prompt `Wave Address (SWP 32bit Address) ? : 0x`. Dumps 256 words (512 bytes) of wave memory
  from that address (routine `0x27D78`) as rows of an address and 8 words, with the header
  `Address 0000 0000 0001 0001 0002 0002 0003 0003`: each 32-bit SWP address holds two words, which is
  why every address appears twice. No `More?` prompt; one page.
- **`wn`**: prompts `Waveform number? :` and `Keybank number? :`, asks the sound-engine side to translate
  them to the wave's start address (a small request packet sent with `0x8C137FF0`), then dumps from that
  address exactly like `wd`.
- **`wds`, `wns`**: the same two commands with a flag (RAM `0x8C1B20F4`) set to 1. The dump then reads
  wave memory through a second routine (`0x8C1381E4`, with the address doubled) instead of
  `0x8C1381D8`. The help text labels these "(SSI)"; what SSI refers to is not known [?].

## 8. Misc: sequencer data and hardware

- **`vs`**: SCSI inquiry table `ID VenderID ProductID Revision`, one row per SCSI ID (`SCS%d0` entries).
  **The Motif ES firmware has no `vs`.**
- **`fd`**: dumps the sequencer FAT (the block-allocation table of sequence data). Prints
  `FAT used count`, `FAT max count (SEQ)` and `FAT max count (SQF)`, each as decimal and hex, then the
  table 16 entries per row (`%.4X`, columns `0000`-`000F`) with `More? (Y/N)` paging.
- **`sf`**: scans the same FAT for faults and prints `Illegal 0xFEFE! at 0x%.4X` and
  `Double booking! at 0x%.4X` for each, then `Checked map...`. `0xFEFE` is the "unused entry" value
  (the same value seen in the pattern files' phrase table).
- **`pi`**: profiler initialise. Clears a 100-entry mark table (RAM `0x8C500640`), registers an interrupt
  handler with `def_int` (failure prints `[CNS] def_int() failed! err:%d`), sets the interrupt priority
  and starts the SH watchdog timer in interval-timer mode (`WTCNT` `0xFFFFFF84` = 0, `WTCSR` `0xFFFFFF86`
  = `0xA582`) as the profiler clock. **[INF]** for the "interval timer" reading.
- **`pv`**: profiler view. Prints `label ticks time[msec]` and one row per mark (`%2d %8d %10.2f`).
- **`cs`**: program-area checksum. Sums every byte from runtime `0x8C010000` through `0x8C1EF000` and
  prints the low byte as `[CNS] Program Area SUM = 0x%.2X`. It skips two ranges: the RAM block
  `0x8C1B20B0`-`0x8C1B80AF` and the console's own parameter words at `0x8C011AB4`-`0x8C011ACC`.
- **`rs`**: reset. Prints `GOOD BYE!`, makes a kernel trap call, then writes `0xA500`, `0x5A00`, `0xA5C7`
  to the SH watchdog registers (`WTCSR` stop, `WTCNT` = 0, `WTCSR` = watchdog mode, enabled, slowest
  clock) and spins until the watchdog overflows and resets the CPU. **[INF]** (register addresses and
  values; the reset itself was not observed.)

## 9. Motif ES firmware

`0745OS_.PGM` (Motif ES6/7/8 OS v1.08, file header `MOTIF2 OS File`) has the same console. The help banner
reads `Last update: Jan 17 2006`; strings are at file offset `0x2FB3C9` (banner) and `0x2FB7E5` (menu).
The menu is identical to the RS7000's **except `vs` is missing** (its menu line, its command string and its
`VenderID`/`ProductID` output text are absent). The wave-dump, FAT, `Waveform NumOfKeyBank` and `1block =
32kw` strings are present. The ES handler code was **not** disassembled: that the commands behave as above
is assumed from the matching names and prompts.

## 9a. Serial hardware of the console (ES and RS7000)

The console's character driver talks to the SH-3 on-chip **SCI channel 0** (registers 0xFFFFFE80 SCSMR,
+2 SCBRR, +4 SCSCR, +6 SCTDR, +8 SCSSR, +0xA SCRDR; the code loads `0xFE80` with `mov.w` and sign-extends it).
Full register model: `docs/SCI0_register_model.md` and `tools/sci0_model.py` in the RS7007 emulator project
(https://github.com/sneak-thief/RS7007), not in this repository.
- Init (RS7000 ROM 0x26228, ES 0x2F5FC; the two are identical except one constant): IPRB SCI priority = 15,
  SCSMR = 0 (8N1, async, Pphi), **SCBRR = 15 (RS7000) / 26 (ES)**, then TE, TIE, RE, RIE set (SCSCR = 0xF0).
  Bit rate = Pphi/512 (RS7000) and Pphi/864 (ES); Pphi is not in the firmware.
- ES: tx routine 0x2F6C8; RX interrupt handler 0x2F7D4 (RDRF, 16-byte ring at 0x8C411684); error handler
  0x2F76C (ORER/FER/PER = 0x20/0x10/0x08); TX handler 0x2F82C (clears TIE). RS7000: ERI 0x26398, RXI 0x26400,
  TXI 0x26458, tx routine 0x262F4.
- The OS does not touch SCPCR/SCPDR (0xA4000116/0xA4000136): the pin function is set up outside the update file.
  (An earlier version of this section said the OS never programs SCI0 at all; that was wrong, the init above was
  missed. Corrected.)
- The SCIF (channel 2, 0xA4000150..) is used by a different driver in the ES (ROM 0x35BB4, 0x3657C, 0x36A0E),
  not by the console.
- Pins: the Linux SH7709 driver reads SCPDR bit 0 as RxD0 of SCI0, so RxD0 = SCPT[0]. The Motif ES schematic
  labels both CPU pins SCPT[0]: pin 198 RxD0/SCPT[0] and pin 191 TxD0/SCPT[0] (README.md, "Motif ES: Hardware").
- Where to solder: RS7000 header CN23 **[HW]**; Motif ES at IC42 pin 5 and R243 (from the schematic); Motif at R46
  and R390 (from a schematic). See README.md.

## 10. Open points

1. Pphi on the ES (SCBRR 26) and the Motif. On the RS7000 (SCBRR 15) 38,400 baud works **[HW]**, so Pphi is close
   to 19.7 MHz; the pin mux (SCPCR) is set outside the OS update, and is in place on a production unit.
2. What the "SSI" access path of `wds`/`wns` is, and what `t` in MyDebugger tests.
3. Exact meaning of the `to` percentage columns and the trace ATTR codes (inferred from the strings).
4. The ES handlers, and whether the ES `wd`/`wn` read the same sound-chip interface.
5. ~~Whether the console is active in a retail unit~~: it is, on the RS7000 **[HW]** (the command replies are
   printed even with `cns_printf` output off, which only gates other tasks' messages). Not yet seen on a Motif or
   Motif ES.
