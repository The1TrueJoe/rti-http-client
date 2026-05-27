#!/usr/bin/env python3
"""
Pure-Python RTI .rtidriver packager.
Implements CFB (Compound File Binary) v3 from scratch — no cfb npm, no SheetJS
watermark, no post-processing hacks.
"""
import json
import struct
import zlib
from pathlib import Path

ROOT       = Path(__file__).parent.parent
DRIVER_DIR = ROOT / 'driver'
DIST_DIR   = ROOT / 'dist'

# ── CFB constants ─────────────────────────────────────────────────────────────
SECTOR_SIZE = 512
MINI_SIZE   = 64
CUTOFF      = 4096          # streams shorter than this go in the mini-stream

FREESECT   = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT    = 0xFFFFFFFD
NOSTREAM   = 0xFFFFFFFF    # "no sibling / child"

EMPTY  = 0
STREAM = 2
ROOT_T = 5                  # root storage entry type

BLACK = 1
RED   = 0


# ── Directory entry builder ───────────────────────────────────────────────────
def dir_entry(name, etype, color, left, right, child, start, size):
    e = bytearray(128)
    if etype != EMPTY and name:
        enc = (name + '\x00').encode('utf-16le')
        e[0:len(enc)] = enc
        struct.pack_into('<H', e, 64, len(enc))   # name length incl. NUL
    e[66] = etype
    e[67] = color
    struct.pack_into('<I', e, 68,  left  & 0xFFFFFFFF)
    struct.pack_into('<I', e, 72,  right & 0xFFFFFFFF)
    struct.pack_into('<I', e, 76,  child & 0xFFFFFFFF)
    struct.pack_into('<I', e, 116, start & 0xFFFFFFFF)
    struct.pack_into('<I', e, 120, size  & 0xFFFFFFFF)
    return bytes(e)


# ── Core builder ─────────────────────────────────────────────────────────────
def build_cfb(streams):
    """
    streams: list of (name, data_bytes) in desired directory order.
    Small streams (<CUTOFF) go in the mini-stream container.
    Large streams (>=CUTOFF) go in regular sectors.
    Returns the raw bytes of a valid CFB v3 file.
    """
    mini_streams = [(n, d) for n, d in streams if len(d) <  CUTOFF]
    reg_streams  = [(n, d) for n, d in streams if len(d) >= CUTOFF]

    # ── Pack mini-stream ──────────────────────────────────────────────────────
    ms_buf    = bytearray()
    ms_start  = {}          # name -> start mini-sector index
    for name, data in mini_streams:
        ms_start[name] = len(ms_buf) // MINI_SIZE
        padded = data + b'\x00' * (-len(data) % MINI_SIZE)
        ms_buf += padded
    n_mini_sectors = len(ms_buf) // MINI_SIZE
    root_stream_size = len(ms_buf)              # unpadded; pad to sector below
    ms_buf += b'\x00' * (-len(ms_buf) % SECTOR_SIZE)
    root_n_sects = len(ms_buf) // SECTOR_SIZE

    # ── Sector layout ─────────────────────────────────────────────────────────
    # 0        : FAT sector
    # 1        : Directory sector 0   (SIDs 0–3)
    # 2        : Directory sector 1   (SIDs 4–7)
    # 3        : miniFAT sector
    # 4 ..     : Root stream  (mini-stream container)
    # 4+root_n : Regular streams
    S_FAT  = 0
    S_DIR0 = 1
    S_DIR1 = 2
    S_MFAT = 3
    S_ROOT = 4

    reg_starts = {}
    cur = S_ROOT + root_n_sects
    for name, data in reg_streams:
        reg_starts[name] = cur
        cur += (len(data) + SECTOR_SIZE - 1) // SECTOR_SIZE
    total_sectors = cur

    # ── FAT ───────────────────────────────────────────────────────────────────
    fat = [FREESECT] * 128      # one FAT sector holds 128 entries (enough for us)
    fat[S_FAT]  = FATSECT
    fat[S_DIR0] = S_DIR1
    fat[S_DIR1] = ENDOFCHAIN
    fat[S_MFAT] = ENDOFCHAIN
    for s in range(S_ROOT, S_ROOT + root_n_sects - 1):
        fat[s] = s + 1
    if root_n_sects > 0:
        fat[S_ROOT + root_n_sects - 1] = ENDOFCHAIN
    for name, data in reg_streams:
        s0 = reg_starts[name]
        n  = (len(data) + SECTOR_SIZE - 1) // SECTOR_SIZE
        for s in range(s0, s0 + n - 1):
            fat[s] = s + 1
        fat[s0 + n - 1] = ENDOFCHAIN

    # ── miniFAT ───────────────────────────────────────────────────────────────
    mfat = [FREESECT] * 128
    for name, data in mini_streams:
        s0 = ms_start[name]
        n  = (len(data) + MINI_SIZE - 1) // MINI_SIZE
        for i in range(n - 1):
            mfat[s0 + i] = s0 + i + 1
        mfat[s0 + n - 1] = ENDOFCHAIN

    # ── Directory entries ─────────────────────────────────────────────────────
    # SID 0 = Root, SID 1..N = streams.
    # MS-CFB requires directory entries sorted by (len(name), name.upper()) so
    # that parsers using BST lookup can find every stream.  A right-chain whose
    # names are not in ascending CFB order causes streams to be unreachable.
    all_names = sorted(
        [n for n, _ in mini_streams] + [n for n, _ in reg_streams],
        key=lambda n: (len(n), n.upper()),
    )
    n_streams = len(all_names)

    entries = []

    # Root Entry
    entries.append(dir_entry(
        'Root Entry', ROOT_T, BLACK,
        NOSTREAM, NOSTREAM, 1 if n_streams else NOSTREAM,
        S_ROOT if root_n_sects else ENDOFCHAIN,
        root_stream_size,
    ))

    # Stream entries — simple right-chain
    all_data = dict(mini_streams + reg_streams)
    for i, name in enumerate(all_names):
        sid   = i + 1
        right = (sid + 1) if sid < n_streams else NOSTREAM
        if name in dict(mini_streams):
            s0   = ms_start[name]
            size = len(all_data[name])
        else:
            s0   = reg_starts[name]
            size = len(all_data[name])
        entries.append(dir_entry(
            name, STREAM, BLACK,
            NOSTREAM, right, NOSTREAM,
            s0, size,
        ))

    # Pad to exactly 8 entries (2 directory sectors)
    empty_e = dir_entry('', EMPTY, RED, NOSTREAM, NOSTREAM, NOSTREAM, 0, 0)
    while len(entries) < 8:
        entries.append(empty_e)

    # ── CFB header (512 bytes) ────────────────────────────────────────────────
    hdr = bytearray(512)
    hdr[0:8] = bytes([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1])
    struct.pack_into('<H', hdr, 24, 0x003E)     # minor version
    struct.pack_into('<H', hdr, 26, 0x0003)     # major version (v3)
    struct.pack_into('<H', hdr, 28, 0xFFFE)     # byte order (LE)
    struct.pack_into('<H', hdr, 30, 9)          # sector size log2 (512)
    struct.pack_into('<H', hdr, 32, 6)          # mini-sector size log2 (64)
    struct.pack_into('<I', hdr, 40, 0)          # num dir sectors (v3 → 0)
    struct.pack_into('<I', hdr, 44, 1)          # num FAT sectors
    struct.pack_into('<I', hdr, 48, S_DIR0)     # first dir sector
    struct.pack_into('<I', hdr, 52, 0)          # transaction signature
    struct.pack_into('<I', hdr, 56, CUTOFF)     # mini stream cutoff
    struct.pack_into('<I', hdr, 60, S_MFAT)     # first miniFAT sector
    struct.pack_into('<I', hdr, 64, 1)          # num miniFAT sectors
    struct.pack_into('<I', hdr, 68, ENDOFCHAIN) # first DIFAT sector (none)
    struct.pack_into('<I', hdr, 72, 0)          # num DIFAT sectors
    struct.pack_into('<I', hdr, 76, S_FAT)      # DIFAT[0] = FAT sector index
    for i in range(1, 109):
        struct.pack_into('<I', hdr, 76 + i * 4, FREESECT)

    # ── Assemble ──────────────────────────────────────────────────────────────
    out = bytearray()
    out += hdr                  # header

    # FAT sector
    fat_sec = bytearray(SECTOR_SIZE)
    for i, v in enumerate(fat):
        struct.pack_into('<I', fat_sec, i * 4, v)
    out += fat_sec

    # Directory sectors (2 × 512 = 1024 bytes)
    dir_sec = bytearray()
    for e in entries:
        dir_sec += e
    assert len(dir_sec) == 2 * SECTOR_SIZE
    out += dir_sec

    # miniFAT sector
    mfat_sec = bytearray(SECTOR_SIZE)
    for i, v in enumerate(mfat):
        struct.pack_into('<I', mfat_sec, i * 4, v)
    out += mfat_sec

    # Root stream (mini-stream container)
    out += ms_buf

    # Regular streams (sector-padded)
    for name, data in reg_streams:
        out += data + b'\x00' * (-len(data) % SECTOR_SIZE)

    return bytes(out)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    pkg     = json.loads((ROOT / 'package.json').read_text())
    version = pkg['version']

    DIST_DIR.mkdir(exist_ok=True)

    script_bytes = (
        (DRIVER_DIR / 'http_client.js')
        .read_text('utf-8')
        .replace('__VERSION__', version)
        .encode('utf-8')
    )
    help_bytes = (DRIVER_DIR / 'instructions.rtf').read_bytes()

    manifest_bytes = (
        (DRIVER_DIR / 'DriverManifest.template.xml')
        .read_text('utf-8')
        .replace('__VERSION__',     version)
        .replace('__SCRIPT_SIZE__', str(len(script_bytes)))
        .replace('__HELP_SIZE__',   str(len(help_bytes)))
        .encode('utf-8')
    )

    # Stream order determines SID order in the directory.
    streams = [
        ('ConfigSettings.xml', (DRIVER_DIR / 'ConfigSettings.xml').read_bytes()),
        ('DriverManifest',     manifest_bytes),
        ('DynamicConfigInfo',  (DRIVER_DIR / 'DynamicConfigInfo').read_bytes()),
        ('SystemEvents.xml',   (DRIVER_DIR / 'SystemEvents.xml').read_bytes()),
        ('SystemFunctions.xml',(DRIVER_DIR / 'SystemFunctions.xml').read_bytes()),
        ('http_client.js',     zlib.compress(script_bytes, 9)),
        ('instructions.rtf',   zlib.compress(help_bytes, 9)),
    ]

    data   = build_cfb(streams)
    out    = DIST_DIR / 'Simple HTTP Client.rtidriver'
    out.write_bytes(data)

    print(f'Packaged {out}')
    print(f'Script size: {len(script_bytes)} bytes; help size: {len(help_bytes)} bytes')
    print(f'Output size: {len(data)} bytes')


if __name__ == '__main__':
    main()
