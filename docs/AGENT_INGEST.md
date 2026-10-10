# 核心 Agent 强化（第二轮）：课表导入与学习文件格式全覆盖

本轮（2026-10-07）强化核心 agent 的两件日常重活：**导入课表**与**学习文件上传识别**。
两条链路共用同一套「内容嗅探 + 有界解析 + 中文可操作提示」的基础设施，
因此「新增一种格式」的成本从「改三处」降到「登记一行」。

- 后端回归 **625 项**通过；Web / Mobile 各 **77 项**通过，双端生产构建成功。
- 本轮新增用例 257 项：格式登记与嗅探 115、上传格式矩阵 22、抽取引擎边界与降级 31、
  Agent 课表事务 45、课表新来源 39、既有课表/网格用例追加 5。

---

## 1. 学习文件上传识别：常见格式全覆盖

### 1.1 单一事实来源：`backend/app/ingest/formats.py`

110 个扩展名 / 12 个分组，每项登记「分组 · 中文名 · 抽取 kind · 是否 OCR · 备注」：

| 分组 | 代表格式 |
| --- | --- |
| 文档 | .pdf .docx .doc .odt .rtf .txt .md .tex .log |
| 演示文稿 | .pptx .ppt .odp |
| 表格 | .xlsx .xlsm .xls .ods .csv .tsv |
| 电子书 | .epub .mobi .azw3 |
| 代码笔记本 | .ipynb |
| 网页与标记 | .html .htm .xhtml .xml |
| 结构化数据 | .json .yaml .yml .toml .ini .ics |
| 代码与脚本 | .py .js .ts .tsx .java .c .cpp .go .rs .sql .sh …（34 种） |
| 字幕与听记 | .srt .vtt .ass .ssa .lrc |
| 图片（OCR） | .png .jpg .jpeg .webp .bmp .gif .tif .tiff .heic .heif .avif |
| 压缩包 | .zip .tar .tar.gz .tgz .tar.bz2 .tbz2 .tar.xz .txz .gz .bz2 .xz（可解包）· .7z .rar .dmg .zst（给指引） |
| 音视频 | .mp3 .wav .m4a .flac .ogg .mp4 .mov .mkv .avi .webm … |

`GET /api/ingest/formats` 把这个目录暴露给双端：上传 `accept`、格式矩阵面板、
格式徽标、摘要文案全部由它派生。`supported=false` 表示「认得出来但本机做不了」
（缺 HEIC 解码器、音视频不转写），前端灰显并展示 `note` 里的替代做法——
**不承诺做不到的事**。

### 1.2 格式判定：内容优先，扩展名不可信

`detect_format()` 按**魔数 + 容器内部结构**判定真实格式：

- ZIP 容器看内部结构：`word/document.xml` → .docx、`xl/workbook.xml` → .xlsx、
  `ppt/presentation.xml` → .pptx、根部 `mimetype` 条目 → .odt/.ods/.odp/.epub；
- OLE2 复合文档扫目录区流名：`WordDocument` → .doc、`Workbook`/`Book` → .xls、
  `PowerPoint Document` → .ppt；
- 图片/音视频/RTF/PDF 按文件头；文本类再按内容细分 HTML / XML / JSON / VTT / ICS；
- 扩展名与内容不一致时（`mismatch=true`）**按内容解析并提示用户**；
- 例外：`.ipynb`/`.srt` 的内容会被嗅探成 JSON/纯文本，此时**听扩展名的**
  （专门解析器优先于通用容器）。

### 1.3 中文编码：GBK 与 Big5 的互相误码怎么解

编码阶梯：UTF-8(BOM) → UTF-16(BOM) → UTF-8 → GB18030 → Big5 → Shift-JIS → EUC-KR → CP1252。

难点在于 **GBK 与 Big5 会互相把对方的字节解成合法汉字**（例：`電腦網路` 的 Big5 字节
按 GB18030 解出来是「筿福呼隔」）。纯靠「能不能解」无法判断，因此对每种候选解码结果打分：

- 高频常用汉字 +3，生僻汉字 0，CJK 扩展区 −3；
- 假名/谚文 +2（本语言正文，且不可能是中文解码的副产物）；
- 拉丁重音字母 +1、ASCII +1（**不能更高**：cp1252 能解出任何字节，
  给高权重会把所有 GBK 中文抢走——实测 26 分 vs 25 分）；
- 取最高分，同分按阶梯顺序。

简繁/日/韩/西欧 9 类语料实测全部正确。非 UTF-8 会在预览里提示实际编码。

### 1.4 抽取器分工

| 模块 | 负责 |
| --- | --- |
| `ingest/office.py` | PDF（**子进程**解码，防原生崩溃）、Word（正文+表格+页眉页脚按序交错）、PPT（文本框+表格+图表数据+演讲者备注）、Excel（逐表渲染，日期不丢）、旧版 .doc/.ppt（OLE2 有界文本抓取）、ODF（odt/ods/odp） |
| `ingest/textfmt.py` | 纯文本/代码（附顶层定义摘要）、CSV·TSV（表头+行号）、HTML、RTF、Jupyter Notebook、字幕、JSON/YAML/XML、编码阶梯 |
| `ingest/ebook.py` | EPUB（按 OPF spine 顺序）、MOBI/AZW3（PalmDOC LZ77 解压） |
| `ingest/archive.py` | 压缩包有界展开：zip / tar（含 tar.gz·tar.bz2·tar.xz，**流式**读）/ 单文件 gz·bz2·xz（解压后按真实内容抽取）；文本类直接解码、二进制类落临时文件复用通用抽取器 |
| `ingest/files.py` | 按 kind 分派 + 扫描版 PDF 自动 OCR + 统一降级与中文提示 |
| `ingest/ocr.py`·`ocrgrid.py` | OCR 通道与坐标网格重建（图片与扫描 PDF 共用） |

新增格式只需在 `formats.py` 登记一行 + 在 `files.py` 挂一个 handler，
上传接口、格式目录、压缩包展开、Agent 的文件理解**自动继承**。

### 1.5 边界与降级（都实测过）

- **大文件**：文本类按 `limit×4` 字节上限从磁盘读取（上传上限 512MB 也不会整载入内存）；
  RTF/MOBI 同理；按字节截断不会把末尾半个汉字误判成乱码（增量解码器丢弃不完整尾部）。
- **压缩包**：条目 ≤20、单条 ≤8MB、解压总量 ≤24MB，加密/超限条目跳过并说明；
  嵌套压缩包只展开一层（防 zip 套 zip 指数放大）；包内 `__MACOSX`·`._*`·`Thumbs.db`
  这类系统元数据静默跳过，不污染正文。
- **图片**：OCR 前只读 header 校验像素（单边 ≤12000px、总像素 ≤5000 万，防解压炸弹）；
  HEIC/HEIF 缺解码器时提示「先转 JPG/PNG」而不是「文件已损坏」。
- **扫描版 PDF**：文本层不足阈值时栅格化 + OCR（前 10 页 / 180 DPI），临时文件必定清理；
  OCR 不可用时给「改用文字版」的指引。
- **旧版二进制**：.doc/.ppt 只能尽力抓文本（格式与图片会丢），明确提示另存为 .docx/.pptx。
- **MOBI/AZW3**：只支持无压缩与 PalmDOC；Kindle 的 HUFF/CDIC 明确提示用 Calibre 转 EPUB。
- **音视频**：不联网、无本地语音识别 → 明确说「暂不支持离线转写」并给替代做法。
- **未知类型**：提示「可转成 PDF/TXT 后重试」，绝不 500。

### 1.6 上传接口回包（`POST /api/materials/upload`）

```jsonc
{
  "extract": { "tasks": [], "materials": [], "warnings": [], "engine": "rule" },
  "file_name": "第4讲 调度.pptx",
  "extracted_text_preview": "…",
  "stored_path": "…/data/files/<uuid>.pptx",
  "text_chars": 1234,
  "format": {
    "ext": ".pptx", "label": "PowerPoint 演示文稿", "group": "slide",
    "group_label": "演示文稿", "kind": "pptx", "ocr": false, "supported": true,
    "note": "含表格、图表与演讲者备注",
    "detected_ext": ".pptx", "declared_ext": ".ppt", "container": "zip",
    "mismatch": true, "sniffed": true
  }
}
```

`mismatch=true` 表示扩展名与内容不一致且**已按实际内容解析**；`extract.warnings`
里会带上「按 X 编码解析」「扫描版 PDF 来自 OCR」「旧版二进制格式」这类可核对的信息，
与 `extract` 的 DDL/资料建议一起展示在确认入库前。

### 1.7 压缩包：常见打包格式全覆盖（本轮扩充）

原来只支持 `.zip`，而老师发的资料包五花八门：Windows 上是 `.zip`/`.rar`/`.7z`，
Linux 与 macOS 课上常见 `.tar.gz`，还有「把一份讲义压一下」的 `.gz`/`.bz2`/`.xz`。
本轮把**标准库能解开的全部解开**，解不开的给可操作指引：

| 写法 | 处理 |
| --- | --- |
| `.zip` | `zipfile` 有界展开 |
| `.tar`·`.tar.gz`·`.tgz`·`.tar.bz2`·`.tbz2`·`.tar.xz`·`.txz` | `tarfile` **流式**读取（`r\|gz` 等），几十 GB 的 tar.gz 也不会整包进内存 |
| `.gz`·`.bz2`·`.xz`（单文件压缩流） | 解压后**按真实内容**抽取：`讲义.txt.gz` 出正文、`报告.docx.gz` 出 Word 正文、`课程.tar.gz` 自动转成整包展开 |
| `.7z`·`.rar`·`.dmg`·`.zst` | 登记但 `supported=False`，提示「先用 7-Zip / WinRAR 解压后上传包内文档」——不是「无法识别的文件」 |

三个实现要点：

1. **两段式扩展名**：`normalize_ext` 先试「最后两段」再退回「最后一段」，所以
   `资料.tar.gz` 与 `资料.tgz` 归到同一个规范扩展名（`.tar.gz`），格式徽标与中文名不乱；
   `.tgz`/`.tbz`/`.tbz2`/`.txz` 这些别名也随 `GET /api/ingest/formats` 的 `aliases`
   下发给双端，前端拖入 `资料.tgz` 不会被 `accept` 误拦（否则「后端能收、前端先拦」）。
2. **tar 嗅探要校验校验和**：只看偏移 257 的 `ustar` 不够稳（普通文件偶合会被当成压缩包），
   因此 `is_tar()` 连首块校验和一起核（校验和字段按 POSIX 当作 8 个空格参与计算）。
   gz/bz2/xz 则**解压头 512 字节**再看里面是不是 tar，从而区分「整包」与「单文件」。
3. **包内条目名**：上传落盘是 uuid 名，压缩包展开时靠 `archive.original_name()`
   把真实文件名带进去，正文里显示的才是「[包内文件] 讲义.txt」而不是一串 uuid；
   包内的 `__MACOSX/`、`._*` 资源叉、`Thumbs.db` 等系统元数据静默跳过。

护栏沿用原口径（条目 ≤20、单条 ≤8MB、解压总量 ≤24MB，嵌套包只展开一层），
但多了一层「压缩流」语义：包内 `套娃.tar.gz` 不展开，而包内 `笔记.txt.gz`
（解压后是一份文档，不是又一个包）会解出来——只压缩一层不构成指数放大。

本轮实测数量（`pytest -q` / `npm test`）：后端 **694 项**、Web **104 项**、
Mobile **100 项**，两端 `tsc` 与生产构建通过。压缩包相关新增/改写用例 23 项：
`test_ingest_extract.py`（tar 家族 7 · 单文件压缩流 3 · gzip 包文档 · 嵌套语义 ·
系统元数据跳过 · tar 护栏 · 条目数触顶说明 · 坏包「可能已损坏」4 · 7z/rar 指引）、
`test_upload_formats.py`（tar.gz 上传 · 单文件 gz 上传 · 7z 指引 · 目录接口压缩包覆盖）、
双端 `formats.test.mjs`（别名与两段式扩展名）。

---

## 2. 课表导入：八类来源

| # | 来源 | 解析方式 |
| --- | --- | --- |
| ① | Excel (.xlsx/.xlsm/.xls) | openpyxl / xlrd，多工作表取数据最多的那张 |
| ② | CSV / TSV | 标准库，UTF-8/GB18030，自动判分隔符 |
| ③ | ODS | ElementTree 读 `content.xml`，展开 `number-columns-repeated`（有上限） |
| ④ | **PDF** | PyMuPDF `find_tables` 优先；失败把词框交给 `ocrgrid.boxes_to_grid` 做坐标网格重建；多页合并；**扫描版自动 OCR**（≤10 页 / 160 DPI） |
| ⑤ | **网页 HTML** | `<table>` 解析并**展开 rowspan/colspan 合并单元格**（教务系统打印页大量使用） |
| ⑥ | **Word .docx** | python-docx 读表格；无表格时按段落成列 |
| ⑦ | iCal .ics / JSON | 周循环事件展开成周次、单双周/EXDATE 节假日排除；JSON 支持 `courses/slots` 与扁平 `course_list` |
| ⑧ | 截图/照片 | PNG·JPG·WEBP·GIF·BMP·TIFF·AVIF → OCR 坐标重建网格 → 失败或漏节次列时 **LLM 智能兜底** |

**统一网格**：所有来源先还原成「行 = 节次/时刻、列 = 星期」的字符串网格，再走同一套
解析（星期表头定位列、节次/时刻标签定位行、单元格内就地抽取课程名/周次/单双周/老师/地点，
左标签列不会被误当成星期列）。因此新增来源不会各写一套解析逻辑。
全空的尾列会被裁掉（ODS/Excel 导出常补几十个空列），解析结果不变、网格更干净。

规模护栏：网格 ≤200 行 × 30 列、PDF ≤20 页 / 单页 ≤20 表 / ≤2 万词框、HTML ≤100 万字符 / ≤50 表、
ODS `content.xml` ≤8MB（`number-columns/rows-repeated` 夹紧到行列上限）、Word ≤50 表 / ≤500 段。

预览响应新增 `format_label`（「PDF 课表」「网页课表」「ODS 课表」「课表截图（AI 辅助）」…），
`format` 取值扩展为 `ics|json|excel|csv|ods|pdf|html|word|image`。
两个刻意的口径：

- **扫描版 PDF 的 `format` 报 `pdf`（不是 `image`）**：用户上传的就是 PDF，
  内部走 OCR 网格通道不影响来源展示，`format_label` 也仍是「PDF 课表」；
  只有真正的图片上传才报 `image`。
- **扩展名不再可信**：`.docx` 命名成 `.xls`、`.html` 命名成 `.xlsx` 都按内容解析；
  老式 `.doc`/`.ppt`/`.zip` 会得到「请另存为 .docx/.pptx / 先解压 / 改用表格」这类可操作提示。

**PyMuPDF 的导入纪律**：PyMuPDF 1.28 的 SWIG 扩展在 import 期会发 DeprecationWarning，
而本仓库 `pytest.ini` 把该告警升级为错误 —— 裸 `import pymupdf` 时异常从 C 扩展初始化里抛出，
**不是可捕获的 ImportError，而是直接把进程打成段错误（SIGSEGV）**。因此课表侧与
资料侧都只允许经 `_pymupdf()`（`warnings.catch_warnings()` 包裹）导入，
资料侧的 PDF 文本抽取本来就跑在子进程里（`ingest/pdfworker.py`）。

**网格行合并的一处实测修复**：`ingest/ocrgrid.py` 的 `_merge_short_rows` 会把
「同一格内错行的文字」并回一行（OCR 截图里表头/节次标签/首行课名各占一条识别线）。
但纯按距离判定有个真实坑：**表头与首行的间距小于节次行距时，表头会被并进第一行**，
星期列随之消失、整份课表直接报「没找到周一…周日表头」（实测表头距首行 60pt、
行距 100pt 时 100% 触发，教务系统导出的 PDF 常见这种紧凑排版）。
现在含 ≥2 个星期名的行**永不参与合并**（表头是网格的列定义），
并补了单元 + 接口两层回归用例。

---

## 3. Agent 事务化课表导入

解析仍在宿主（`app/courseimport*`），**事务语义交给 agent 核心**（`agent_core/courses.py`）。

### 3.1 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/agent/courses/preview` | 提交已解析条目 → 建议（plan），只算不写 |
| POST | `/api/agent/plans/{plan_id}/apply` | 单事务执行（需 `Idempotency-Key`），返回统一回执 |
| POST | `/api/agent/operations/{id}/undo` | 撤销（需 `Idempotency-Key`）：新建的删除留墓碑、合并的还原 slots |
| GET | `/api/agent/capabilities` | 新增能力位 `course_import: true` |

请求：

```jsonc
{
  "entries": [{
    "name": "操作系统", "code": "CS301", "teacher": "张老师", "location": "教一101",
    "slots": [{"weekday": 1, "start": "08:00", "end": "09:40", "weeks": [1,2,3],
               "location": "", "teacher": ""}]
  }],
  "term_start_date": "2026-09-28", "semester": "2026秋"
}
```

响应（`checks`/`actions`/`dependencies` 由 API 层剥离，前端只见结果）：

```jsonc
{
  "plan_id": "…", "intent": "course_import", "expires_at": "…",
  "summary": "导入 2 门课程：新建 1 门、合并 1 门（共 3 个时段）",
  "entries": [{ "name": "操作系统", "exists": false, "existing_id": null,
                "added_slots": 2, "merged_slots": 0, "changed": true, "current": null, "slots": [] }],
  "conflicts": [{ "weekday": 2, "start": "10:00", "end": "10:40",
                  "courses": ["数据库"], "message": "周二「数据库」自身时段重叠（10:00-10:40）" }],
  "warnings": ["检测到 1 处时间冲突，请核对课表"],
  "result": { "new_courses": ["操作系统"], "merged_courses": [], "slot_count": 3, "conflict_count": 0 }
}
```

### 3.2 合并与冲突口径（与宿主一致）

- 按**归一化课名**判同名（NFKC + 去空白 + casefold，比宿主的 `name ==` 更宽，
  `"OS"` 与 `"os"` 会合并——属有意行为）；
- 同一 `weekday + start + end` 的时段**并集 weeks**；`teacher/location/code/semester/start_date`
  **只补空不覆盖**，用户手改过的字段不会被导入覆盖；
- 时间冲突**只提示不阻塞**（扫描「导入后的完整课表」，含未涉及的旧课；
  时段超过 4000 时退化为只查导入范围并在 `warnings` 说明）。

### 3.3 事务与并发保护

- 预览 → 执行 → 撤销全程走既有 `agent_plans` / `agent_operations` 通道：
  幂等键重放只生效一次、plan 30 分钟过期、撤销依赖 `agent_operations` 的 changes；
- `checks` 保证「预览之后数据变了就 409 重新生成」：
  - 既有行：整行快照比对（有人改过这门课 → 409，避免覆盖用户手改的课表）；
  - **新建行：`{"kind": "absent"}` 同名必须仍然不存在**——两个预览都判「新建」同一门课时，
    后执行的 409 而不是插入重复课程（这是本轮补上的并发保护）；
- `execute_plan` 里把 `sqlite3.IntegrityError`（唯一索引/外键冲突）统一转成
  409「建议生成后学习数据已变化，请重新生成」，不给用户看 500；
- 校验从严：≤100 门课、每门 ≤50 时段、`weeks` 1..60、单时段 ≤12h、整批 ≤1000h、
  `start/end` 必须是非空 `HH:MM` 且 `end > start`（宿主解析产物天然满足）。

---

## 4. 复现

```bash
cd backend && .venv/bin/python -m pytest -q          # 694 项（含压缩包多格式扩充 23 项）
cd web && npm test && npm run build                  # 104 项 + 生产构建
cd mobile && npm test && npm run build               # 100 项 + 生产构建
```

本轮相关用例：

| 文件 | 覆盖 |
| --- | --- |
| `backend/tests/test_ingest_formats.py` | 登记表完整性、别名、目录接口、魔数/容器嗅探、扩展名纠正、HEIC 降级 |
| `backend/tests/test_upload_formats.py` | 真实文件端到端：docx/pptx/xlsx/odt/epub/ipynb/html/rtf/srt/代码/压缩包/改名文件/音视频/HEIC/目录接口 |
| `backend/tests/test_ingest_extract.py` | 编码阶梯、文本整理、RTF/字幕/Notebook 细节、**压缩包多格式**（tar 家族/单文件 gz·bz2·xz/tar 护栏/嵌套包/系统元数据跳过/7z·rar 指引）、OLE2 抓取、扫描 PDF 的 OCR 兜底接线、大文件读取上限 |
| `backend/tests/test_course_import_formats.py` | PDF/HTML（含合并单元格）/DOCX/ODS 课表、改名文件、损坏文件 422 |
| `backend/tests/test_agent_courses.py` | 新建/合并判定、执行与撤销、幂等重放、过期、并发抢建 409、输入校验、宿主 ORM 与真实路由全链路 |
| `web/tests/formats.test.mjs`·`mobile/tests/formats.test.mjs` | 格式目录归一化、accept 派生、**压缩包别名与两段式扩展名**、摘要、徽标与 mismatch 提示 |

---

## 5. 已知边界与未验证项

1. **HEIC/HEIF 本机不可解码**（未安装 `pillow-heif`）：已登记并在目录里降级标记 +
   给「先转 JPG/PNG」指引，但没有做真实解码验证。
2. **音视频不转写**：不联网、无本地语音识别，属于产品取舍而非缺陷，已在目录里说明。
3. **MOBI/AZW3**：仅 PalmDOC；HUFF/CDIC 压缩的 Kindle 文件提示转换，未做真实样机验证。
4. **旧版 .doc/.ppt**：有界文本抓取，复杂排版（文本框、艺术字）会丢；真实教务/教师
   老文件样本有限，建议用 Office/WPS 转换后再入库。
5. **PDF 课表**：带表格线的教务系统 PDF 走 `find_tables`，最稳；**纯文字 PDF** 靠词框坐标聚类，
   已知局限是「很宽的行标签框偶尔会被并进邻近的星期列」，已用 `_unmerge_label_column` 补救
   （第一列是周一且每格能切出「第N节 / HH:MM」前缀时拆回两列，拆不动就原样返回），
   真实窄列 PDF 已验证，排版极不规则（多栏、跨页表格）时仍需 AI 兜底或改用 Excel/CSV。
   PDF 解析在**进程内**（不是像资料抽取那样走子进程），极端损坏文件理论上可能影响服务进程；
   若日后要彻底隔离，可照 `ingest/pdfworker.py` 的做法迁移。
6. **OCR 与 LLM 兜底**：测试环境强制关闭 OCR、未配置 LLM，因此这两条路径的
   **接线**有单测覆盖（monkeypatch / 参数化），但**真实识别质量**未在自动化里评估，
   需要在演示机上人工确认。
7. **并发**：同一门课被两个建议同时判为「新建」已由 `absent` 检查拦住（409）；
   跨独立服务器的并发仍未验证。
8. **前端格式矩阵**：`GET /api/ingest/formats` 不可用时静默降级到硬编码兜底列表
   （等价改造前的 accept），上传不会被阻断。
9. **7z/RAR/DMG/ZST 不解包**：不能靠标准库解码，也不引第三方依赖（解码器缺失会把
   「文件损坏」误报给用户）。已在登记表标 `supported=False` 并给「先解压再上传」指引；
   若日后引入 `py7zr`/`unrar`，只需把对应 kind 的 handler 挂上，其余链路自动继承。
   `.zst` 同理（`zstandard` 未装）。
