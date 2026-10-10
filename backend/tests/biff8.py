"""测试用老式 .xls（BIFF8 / OLE2 复合文档）最小生成器。

教务系统导出的「学生个人课表」多为 .xls（BIFF8）。项目里没有 xlwt 这类写库，
而测试要覆盖 xlrd 读取分支，所以这里按规范手写最小可读结构：

- OLE2 复合文档：header + FAT + 目录项（Root Entry / Workbook 两个 entry）；
- Workbook 流：BOF → SST + LABELSST/NUMBER → EOF，字符串统一走 SST 共享表
  （BIFF8 的 Unicode 字符串：cch + grbit + UTF-16LE）。

只用于测试，不进生产代码路径。数值/文本网格均可写。
"""
from __future__ import annotations

import struct

SECTOR = 512
MINI_SECTOR = 64
ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF
FATSECT = 0xFFFFFFFD
NOSTREAM = 0xFFFFFFFF


def _rec(rec_id: int, payload: bytes) -> bytes:
    """BIFF 记录：2 字节 ID + 2 字节长度 + 负载。"""
    return struct.pack("<HH", rec_id, len(payload)) + payload


def _sst(strings: list[str]) -> bytes:
    """SST（0x00FC）：共享字符串表，全部按未压缩 UTF-16LE 写入。

    单条字符串超过 BIFF 记录上限的场景课表里不会出现，故不做 CONTINUE 分片。
    """
    body = struct.pack("<II", len(strings), len(strings))
    for s in strings:
        data = s.encode("utf-16-le")
        body += struct.pack("<HB", len(s), 0x01) + data
    return _rec(0x00FC, body)


def _bof(subtype: int) -> bytes:
    """BOF（0x0809）：0x0005 = workbook globals，0x0010 = worksheet。"""
    return _rec(0x0809, struct.pack("<HHHHII", 0x0600, subtype, 0x0DBB, 0x07CC, 0, 0))


def _label_sst(row: int, col: int, sst_idx: int) -> bytes:
    return _rec(0x00FD, struct.pack("<HHHI", row, col, 0, sst_idx))


def _number(row: int, col: int, value: float) -> bytes:
    return _rec(0x0203, struct.pack("<HHH", row, col, 0) + struct.pack("<d", value))


def _boundsheet(offset: int, name: str) -> bytes:
    """BOUNDSHEET（0x0085）：工作表在 Workbook 流里的偏移 + 名称（UTF-16LE）。

    没有这条记录，xlrd 打开工作簿后会一个 sheet 都看不到。
    """
    encoded = name.encode("utf-16-le")
    return _rec(
        0x0085,
        struct.pack("<IBB", offset, 0, 0) + struct.pack("<BB", len(name), 0x01) + encoded,
    )


def grid_to_xls(grid: list[list[object]]) -> bytes:
    """二维网格（字符串/数字/None）→ BIFF8 .xls 字节。"""
    strings: list[str] = []
    index: dict[str, int] = {}

    def sst_index(s: str) -> int:
        if s not in index:
            index[s] = len(strings)
            strings.append(s)
        return index[s]

    cells: list[bytes] = []
    max_row = len(grid)
    max_col = max((len(r) for r in grid), default=0)
    for r, row in enumerate(grid):
        for c, value in enumerate(row):
            if value is None or value == "":
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(_number(r, c, float(value)))
            else:
                cells.append(_label_sst(r, c, sst_index(str(value))))

    worksheet = (
        _bof(0x0010)
        + _rec(0x0200, struct.pack("<IIHH", 0, max_col, 0, max_row))  # DIMENSIONS
        + b"".join(cells)
        + _rec(0x000A, b"")                                          # EOF
    )
    globals_ = (
        _bof(0x0005)
        + _sst(strings)
        + _rec(0x000A, b"")                                          # globals EOF
    )
    # globals（BOF+SST）之后是 BOUNDSHEET + globals EOF，工作表流紧跟其后；
    # BOUNDSHEET 指向的是工作表流的绝对偏移，用它自己的长度反推。
    globals_body = globals_[:-4]
    placeholder = _boundsheet(0, "课表")
    offset = len(globals_body) + len(placeholder) + 4
    tail = _boundsheet(offset, "课表") + _rec(0x000A, b"") + worksheet
    return _compound_file(globals_body + tail)


def _compound_file(stream: bytes) -> bytes:
    """把 Workbook 流包进最小 OLE2 复合文档（512B 扇区）。

    OLE2 规定：流长度 < 4096 字节必须放进 mini stream（Root Entry 的 SSCS +
    mini FAT），否则读取方会去 mini FAT 里找扇区而报错。小课表流很容易短于
    4096 字节，所以这里用 DIMENSIONS 记录把流补到阈值以上，绕开 mini stream。
    """
    threshold = 4096
    filler = struct.pack("<IIHH", 0, 0, 0, 0)  # DIMENSIONS 负载：表尺寸全 0
    while len(stream) + 4 + len(filler) < threshold:
        stream += _rec(0x0200, filler)
    if len(stream) < threshold:
        pad = threshold - len(stream) - 4
        stream += _rec(0x0200, filler[:pad])

    n_data = max(1, (len(stream) + SECTOR - 1) // SECTOR)
    total_sectors = 2 + n_data          # [0]=FAT, [1]=目录, [2..]=数据
    data = stream.ljust(n_data * SECTOR, b"\x00")

    fat = [ENDOFCHAIN] * total_sectors
    fat[0] = FATSECT                    # FAT 自身的表项
    fat[1] = ENDOFCHAIN                 # 目录只占一个扇区
    for i in range(n_data):
        fat[2 + i] = ENDOFCHAIN if i == n_data - 1 else 2 + i + 1
    fat += [FREESECT] * ((SECTOR // 4) - len(fat))
    fat_sector = struct.pack(f"<{len(fat)}I", *fat)

    directory = _dir_entry("Root Entry", 5, 1, -1, 0)            # 子节点 = Workbook
    directory += _dir_entry("Workbook", 2, -1, -1, len(stream))  # 无子节点/兄弟
    directory = directory.ljust(SECTOR, b"\x00")

    header = bytearray(512)
    header[0:8] = bytes([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1])
    struct.pack_into("<H", header, 24, 0x003E)   # minor version
    struct.pack_into("<H", header, 26, 0x0003)   # major version
    struct.pack_into("<H", header, 28, 0xFFFE)   # little endian
    struct.pack_into("<H", header, 30, 9)        # 扇区大小 2^9 = 512
    struct.pack_into("<H", header, 32, 6)        # mini 扇区 2^6（未使用）
    struct.pack_into("<I", header, 44, 1)        # FAT 扇区数
    struct.pack_into("<I", header, 48, 1)        # 目录起始扇区
    struct.pack_into("<I", header, 56, threshold)    # mini stream 截断阈值
    struct.pack_into("<I", header, 60, ENDOFCHAIN)   # 首个 mini FAT（无）
    struct.pack_into("<I", header, 64, 0)            # mini FAT 扇区数
    struct.pack_into("<I", header, 68, ENDOFCHAIN)   # 首个 DIFAT（无）
    struct.pack_into("<I", header, 72, 0)            # DIFAT 扇区数
    for i in range(109):
        struct.pack_into("<I", header, 76 + i * 4, 0 if i == 0 else FREESECT)
    return bytes(header) + fat_sector + directory + data


def _dir_entry(name: str, obj_type: int, child: int, left: int, size: int) -> bytes:
    """OLE2 目录项（128 字节）：UTF-16LE 名称 + 类型/树指针 + 起始扇区/流长度。

    注意字段位置：first_SID 在偏移 116，流长度（32 位）在偏移 120；
    写成 64 位长度会把 first_SID 覆盖掉，xlrd 就找不到 Workbook 流。
    """
    entry = bytearray(128)
    encoded = (name + "\x00").encode("utf-16-le")
    entry[0:len(encoded)] = encoded
    struct.pack_into("<H", entry, 64, len(encoded))
    entry[66] = obj_type
    entry[67] = 1                                   # 红黑树节点颜色：黑
    struct.pack_into("<i", entry, 68, -1)           # 右兄弟 NOSTREAM
    struct.pack_into("<i", entry, 72, left)         # 左兄弟
    struct.pack_into("<i", entry, 76, child)        # 子节点
    if obj_type == 5:                               # Root Entry：无 mini stream
        struct.pack_into("<i", entry, 116, -2)      # ENDOFCHAIN
        struct.pack_into("<I", entry, 120, 0)
    else:
        struct.pack_into("<i", entry, 116, 2)       # 数据从物理扇区 2 开始
        struct.pack_into("<I", entry, 120, size)
    return bytes(entry)
