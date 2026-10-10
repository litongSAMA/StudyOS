import test from "node:test";
import assert from "node:assert/strict";
import {
  acceptExtensions,
  acceptFor,
  acceptFromCatalog,
  fallbackCatalog,
  refineFallbackCatalog,
  DEFAULT_FORMAT_GROUPS,
  DEFAULT_UNKNOWN_NOTE,
  fileExt,
  findFormat,
  formatBadge,
  formatCount,
  GROUP_LABELS,
  GROUP_ORDER,
  importFormatLabel,
  isRefinableFile,
  mismatchNotice,
  refineAcceptFallback,
  refineAcceptFromCatalog,
  refineGroups,
  refineRejectReason,
  refineSummaryText,
  normalizeCatalog,
  normalizeExt,
  summaryText,
  textCharsText,
  TIMETABLE_ACCEPT,
  unknownNoteOf,
} from "../src/lib/formats.ts";
import { DEFAULT_INGEST_ACCEPT, INGEST_ACCEPT, validateIngestFile } from "../src/agent.ts";

/** 与契约示例同形的接口回包 */
const payload = () => ({
  groups: [
    {
      id: "document",
      label: "文档",
      formats: [
        { ext: ".pdf", label: "PDF 文档", supported: true, ocr: false, note: "扫描版自动转 OCR", group: "document" },
        { ext: ".docx", label: "Word 文档", supported: true, ocr: false, note: "", group: "document" },
      ],
    },
    {
      id: "sheet",
      label: "表格",
      formats: [{ ext: ".xlsx", label: "Excel 表格", supported: true, ocr: false, note: "", group: "sheet" }],
    },
    {
      id: "image",
      label: "图片（OCR 识别）",
      formats: [
        { ext: ".png", label: "PNG 图片", supported: true, ocr: true, note: "离线 OCR 识别", group: "image" },
        {
          ext: ".heic",
          label: "HEIC 图片",
          supported: false,
          ocr: true,
          note: "离线 OCR 识别；本机若缺 HEIC 解码器请先转成 JPG/PNG",
          group: "image",
        },
      ],
    },
  ],
  accept: ".pdf,.docx,.xlsx,.png,.heic",
  extensions: [".pdf", ".docx", ".xlsx", ".png", ".heic"],
  unknown_note: DEFAULT_UNKNOWN_NOTE,
  ocr_available: false,
});

const group = (id, label, exts) => ({
  id,
  label,
  formats: exts.map((ext) => ({ ext, label: `${ext} 文件`, supported: true, ocr: false, note: "", group: id })),
});

test("accept 串归一化：大小写、空白、去重与非法项都被收敛", () => {
  assert.deepEqual(acceptExtensions(" .PDF , .pdf,docx,, *.exe, .x "), [".pdf", ".docx", ".x"]);
  assert.deepEqual(acceptExtensions(undefined), []);
  assert.deepEqual(acceptExtensions(42), []);
  assert.equal(normalizeExt("PDF"), ".pdf");
  assert.equal(normalizeExt(".Tar.Gz"), ".tar.gz");
  assert.equal(normalizeExt("  "), "");
  assert.equal(normalizeExt(".bad ext"), "");
});

test("文件名扩展名：无扩展名/隐藏文件/结尾点都交给后端按内容嗅探", () => {
  assert.equal(fileExt("课件.PDF"), ".pdf");
  // 两段式扩展名要整段取出：否则 .tar.gz 与 accept 里的 .tar.gz 对不上，会被前端误拦
  assert.equal(fileExt("timetable.tar.gz"), ".tar.gz");
  assert.equal(fileExt("讲义.TAR.BZ2"), ".tar.bz2");
  assert.equal(fileExt("备份.tar.xz"), ".tar.xz");
  assert.equal(fileExt("讲义.tgz"), ".tgz");
  assert.equal(fileExt("报告.docx.gz"), ".gz");
  assert.equal(fileExt("课表"), "");
  assert.equal(fileExt(".gitignore"), "");
  assert.equal(fileExt("teacher."), "");
  assert.equal(fileExt(undefined), "");
});

test("acceptFor 只收 supported=true 的格式并去重", () => {
  const groups = [
    { id: "document", label: "文档", formats: [
      { ext: ".pdf", label: "PDF", supported: true, ocr: false, note: "", group: "document" },
      { ext: ".pdf", label: "重复", supported: true, ocr: false, note: "", group: "document" },
      { ext: ".heic", label: "HEIC", supported: false, ocr: true, note: "缺解码器", group: "document" },
    ] },
    { id: "media", label: "音视频", formats: [
      { ext: ".mp4", label: "MP4", supported: false, ocr: false, note: "不转写", group: "media" },
    ] },
  ];
  assert.equal(acceptFor(groups), ".pdf");
  assert.equal(acceptFor([]), "");
  assert.equal(acceptFor(undefined), "");
});

test("acceptFromCatalog 优先用接口 accept，但剔除 supported=false 的格式", () => {
  const catalog = normalizeCatalog(payload());
  assert.equal(catalog.accept, ".pdf,.docx,.xlsx,.png");
  // 接口没给 accept 时按目录派生
  const noAccept = normalizeCatalog({ ...payload(), accept: "" });
  assert.equal(noAccept.accept, ".pdf,.docx,.xlsx,.png");
  // 目录整个为空时兜底到硬编码列表，绝不返回空 accept
  const empty = fallbackCatalog();
  assert.equal(empty.accept, acceptFor(DEFAULT_FORMAT_GROUPS));
  assert.match(empty.accept, /\.pdf/);
  assert.equal(acceptFromCatalog(null), acceptFor(DEFAULT_FORMAT_GROUPS));
  assert.equal(acceptFromCatalog(undefined), acceptFor(DEFAULT_FORMAT_GROUPS));
});

test("兜底目录与旧硬编码 INGEST_ACCEPT 等价（降级后能力不缩水）", () => {
  assert.equal(INGEST_ACCEPT, DEFAULT_INGEST_ACCEPT);
  assert.deepEqual(
    new Set(acceptExtensions(acceptFor(DEFAULT_FORMAT_GROUPS))),
    new Set(acceptExtensions(DEFAULT_INGEST_ACCEPT)),
  );
  assert.deepEqual(DEFAULT_FORMAT_GROUPS.map((g) => g.id), ["document", "slide", "image"]);
  assert.deepEqual(
    DEFAULT_FORMAT_GROUPS.map((g) => g.label),
    [GROUP_LABELS.document, GROUP_LABELS.slide, GROUP_LABELS.image],
  );
});

test("normalizeCatalog：groups 为空/字段缺失/非法载荷都降级到兜底目录，不抛异常", () => {
  for (const raw of [null, undefined, 0, "oops", {}, { groups: [] }, { groups: "x" }, { groups: [null, 1] }]) {
    const catalog = normalizeCatalog(raw);
    assert.equal(catalog.groups.length, DEFAULT_FORMAT_GROUPS.length, JSON.stringify(raw));
    assert.ok(catalog.accept.includes(".pdf"));
    assert.equal(catalog.unknown_note, DEFAULT_UNKNOWN_NOTE);
    assert.equal(catalog.ocr_available, false);
  }
});

test("normalizeCatalog：未知 group 保留、排到已知分组之后并兜底中文名", () => {
  const catalog = normalizeCatalog({
    groups: [
      { id: "weird", label: "", formats: [{ ext: ".zzz", label: "", supported: true }] },
      { id: "slide", label: "", formats: [{ ext: ".pptx", label: "PPT 演示" }] },
      { id: "other", formats: [{ ext: ".bin" }] },
    ],
  });
  assert.deepEqual(catalog.groups.map((g) => g.id), ["slide", "other", "weird"]);
  assert.equal(catalog.groups[0].label, "演示文稿");
  assert.equal(catalog.groups[1].label, "其他");
  assert.equal(catalog.groups[2].label, "weird");
  // 缺 label 的格式退化成扩展名；缺 supported 视为可用
  assert.equal(catalog.groups[2].formats[0].label, ".zzz");
  assert.equal(catalog.groups[2].formats[0].supported, true);
});

test("normalizeCatalog：丢弃脏格式、扩展名全局去重、扩展名清单可派生", () => {
  const catalog = normalizeCatalog({
    groups: [
      { id: "document", formats: [{ ext: "pdf", label: "PDF" }, { ext: ".pdf" }, { ext: "" }, { ext: "  " }, "x", null] },
      { id: "code", formats: [{ ext: ".PY", label: "Python", supported: false, note: "仅登记" }] },
    ],
  });
  assert.deepEqual(catalog.groups.map((g) => g.id), ["document", "code"]);
  assert.deepEqual(catalog.groups[0].formats.map((f) => f.ext), [".pdf"]);
  assert.equal(catalog.groups[0].formats[0].group, "document");
  assert.deepEqual(catalog.groups[1].formats[0].ext, ".py");
  assert.deepEqual(catalog.extensions, [".pdf", ".py"]);
  assert.equal(catalog.accept, ".pdf");
});

test("summaryText：一句话摘要给出总数与分类，超过上限只加「等」", () => {
  const catalog = normalizeCatalog({
    groups: [
      group("document", "文档", [".pdf"]),
      group("sheet", "表格", [".xlsx"]),
      group("slide", "演示文稿", [".pptx"]),
      group("ebook", "电子书", [".epub"]),
      group("code", "代码与脚本", [".py"]),
      group("subtitle", "字幕与听记", [".srt"]),
      group("image", "图片（OCR 识别）", [".png", ".jpg"]),
      group("archive", "压缩包", [".zip"]),
    ],
  });
  assert.equal(summaryText(catalog.groups), "支持 9 种格式 · 文档 / 表格 / 演示 / 电子书 / 代码 / 字幕 / 图片 OCR 等");
  // 少于上限时不加「等」
  assert.equal(summaryText(DEFAULT_FORMAT_GROUPS), "支持 11 种格式 · 文档 / 演示 / 图片 OCR");
  assert.equal(formatCount(catalog.groups), 9);
});

test("summaryText：空目录也给出可用文案，不出现「支持 0 种」", () => {
  const text = summaryText([]);
  assert.match(text, /支持/);
  assert.doesNotMatch(text, /0 种/);
  assert.equal(summaryText(undefined), text);
});

test("formatBadge：format 为 null 时兜底成「未知格式」并带上提示", () => {
  const badge = formatBadge(null);
  assert.equal(badge.label, "未知格式");
  assert.equal(badge.tone, "muted");
  assert.equal(badge.supported, false);
  assert.equal(badge.mismatch, false);
  assert.equal(badge.mismatchNote, "");
  assert.equal(badge.note, DEFAULT_UNKNOWN_NOTE);
  assert.equal(formatBadge(null, "自定义提示").note, "自定义提示");
  assert.equal(formatBadge(undefined).label, "未知格式");
});

test("formatBadge：正常格式给出中文名/分组/字数，label 缺失退回扩展名", () => {
  const badge = formatBadge({
    ext: ".pdf", label: "PDF 文档", supported: true, ocr: false, note: "扫描版自动转 OCR", group: "document",
  });
  assert.equal(badge.label, "PDF 文档");
  assert.equal(badge.groupLabel, "文档");
  assert.equal(badge.tone, "ok");
  assert.equal(badge.mismatchNote, "");
  assert.equal(formatBadge({ ext: ".abc" }).label, ".abc");
  assert.equal(formatBadge({ supported: true }).label, "未知格式");
});

test("formatBadge：不支持 / mismatch 的配色与提示", () => {
  const unsupported = formatBadge({ ext: ".heic", label: "HEIC 图片", supported: false, group: "image" });
  assert.equal(unsupported.tone, "muted");
  assert.match(unsupported.note, /暂不支持/);
  assert.equal(unsupported.groupLabel, "图片（OCR 识别）");

  const mismatch = formatBadge({
    ext: ".pptx", label: "PPT 演示", supported: true, group: "slide",
    detected_ext: ".pptx", declared_ext: ".ppt", mismatch: true, sniffed: true,
  });
  assert.equal(mismatch.tone, "warn");
  assert.equal(mismatch.mismatch, true);
  assert.match(mismatch.mismatchNote, /扩展名与实际内容不一致/);
  assert.match(mismatch.mismatchNote, /声明 \.ppt，实际 \.pptx/);
  assert.match(mismatch.mismatchNote, /按实际内容解析/);
  // group_label 优先于 group 映射，缺失时用扩展名兜底
  assert.equal(formatBadge({ ext: ".pdf", group: "document", group_label: "文档资料" }).groupLabel, "文档资料");
  assert.equal(mismatchNotice(null), "");
  assert.equal(mismatchNotice({ mismatch: false }), "");
  assert.match(mismatchNotice({ mismatch: true }), /按实际内容解析/);
});

test("textCharsText：非法/零值不渲染，正常值带单位", () => {
  assert.equal(textCharsText(12345), "12345 字");
  assert.equal(textCharsText("88"), "88 字");
  assert.equal(textCharsText(0), "");
  assert.equal(textCharsText(-3), "");
  assert.equal(textCharsText(null), "");
  assert.equal(textCharsText(undefined), "");
  assert.equal(textCharsText(NaN), "");
});

test("importFormatLabel：优先中文名，缺失时退回 format 原值", () => {
  assert.equal(importFormatLabel({ format: "pdf", format_label: "PDF 课表" }), "PDF 课表");
  assert.equal(importFormatLabel({ format: "html", format_label: "" }), "html");
  assert.equal(importFormatLabel({ format: "ods" }), "ods");
  assert.equal(importFormatLabel({ format: "  word  ", format_label: "   " }), "word");
  assert.equal(importFormatLabel({}), "未知格式");
  assert.equal(importFormatLabel(null), "未知格式");
});

test("unknownNoteOf 兜底未知类型提示", () => {
  assert.equal(unknownNoteOf({ unknown_note: "转 PDF 重试" }), "转 PDF 重试");
  assert.equal(unknownNoteOf({}), DEFAULT_UNKNOWN_NOTE);
  assert.equal(unknownNoteOf(null), DEFAULT_UNKNOWN_NOTE);
});

test("GROUP_ORDER 与分组常量固定（目录分区顺序契约）", () => {
  assert.deepEqual(GROUP_ORDER, [
    "document", "slide", "sheet", "ebook", "notebook", "markup",
    "data", "code", "subtitle", "image", "archive", "media",
  ]);
  assert.equal(GROUP_LABELS.image, "图片（OCR 识别）");
  assert.equal(GROUP_LABELS.media, "音视频");
});

test("课表导入 accept 覆盖教务系统 Excel/CSV/PDF/网页/Word/iCal/JSON/图片", () => {
  const exts = new Set(acceptExtensions(TIMETABLE_ACCEPT));
  for (const ext of [
    ".ics", ".ical", ".icalendar", ".ifb", ".json", ".xlsx", ".xlsm", ".xls", ".ods", ".csv", ".tsv",
    ".txt", ".docx", ".pdf", ".html", ".htm", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif",
    ".tif", ".tiff", ".avif",
  ]) assert.ok(exts.has(ext), `缺少 ${ext}`);
  // .odt/.doc/.ppt/.zip 后端不做网格解析（会给「另存为 .docx / 先解压」的提示），
  // .heic 本机缺解码器 → 都不放进 accept，避免「可选却必然报错」
  for (const ext of [".odt", ".doc", ".ppt", ".zip", ".heic"]) {
    assert.equal(exts.has(ext), false, `不该承诺 ${ext}`);
  }
  assert.equal(exts.size, acceptExtensions(TIMETABLE_ACCEPT).length);
  assert.equal(acceptFor(DEFAULT_FORMAT_GROUPS).includes(".zip"), false);
});

test("validateIngestFile：默认兜底列表行为不变", () => {
  assert.match(validateIngestFile({ name: "notes.txt", size: 0 }), /为空/);
  assert.match(validateIngestFile({ name: "notes.exe", size: 10 }), /不支持/);
  assert.equal(validateIngestFile({ name: "课件.PDF", size: 10 }), "");
});

test("validateIngestFile：接口返回的 accept 可覆盖默认列表", () => {
  const accept = acceptFromCatalog(normalizeCatalog(payload()));
  assert.equal(accept, ".pdf,.docx,.xlsx,.png");
  assert.equal(validateIngestFile({ name: "课表.xlsx", size: 10 }, accept), "");
  assert.match(validateIngestFile({ name: "旧格式.ppt", size: 10 }, accept), /不支持/);
  assert.match(validateIngestFile({ name: "缺解码器.heic", size: 10 }, accept), /不支持/);
  // 无扩展名放行（后端按魔数嗅探）；accept 非法/为空时不做拦截，避免误伤上传
  assert.equal(validateIngestFile({ name: "课表", size: 10 }, accept), "");
  assert.equal(validateIngestFile({ name: "a.exe", size: 10 }, ""), "");
  assert.equal(validateIngestFile({ name: "a.exe", size: 10 }, " * , "), "");
});

// ---------------------------------------------------------------------------
// 「AI 笔记提炼」可直传的格式（识图 + 文档一条通道）
// ---------------------------------------------------------------------------

test("压缩包：两段式扩展名与 accept 对齐，上传前不会误拦", () => {
  // 后端 accept 里有 .tar.gz，前端的 fileExt 必须取出同一段，否则 tar.gz 必被误拦
  const catalog = normalizeCatalog({
    groups: [
      {
        id: "archive",
        label: "压缩包",
        formats: [
          { ext: ".zip", label: "ZIP 压缩包", supported: true, ocr: false, note: "", group: "archive" },
          { ext: ".tar.gz", label: "TAR.GZ 压缩包", supported: true, ocr: false, note: "", group: "archive" },
          { ext: ".gz", label: "GZIP 压缩文件", supported: true, ocr: false, note: "", group: "archive" },
          {
            ext: ".7z", label: "7-Zip 压缩包", supported: false, ocr: false,
            note: "本机没有 7z 解码器：请先用 7-Zip 解压，把里面的课件/讲义单独上传", group: "archive",
          },
        ],
      },
    ],
    accept: ".zip,.tar.gz,.gz,.7z",
    aliases: { ".tgz": ".tar.gz", ".tbz2": ".tar.bz2" },
  });
  const accept = acceptFromCatalog(catalog);
  assert.equal(accept, ".zip,.tar.gz,.gz");                 // 7z 本机解不开 → 不进 accept
  assert.equal(validateIngestFile({ name: "资料.tar.gz", size: 10 }, accept), "");
  assert.equal(validateIngestFile({ name: "讲义.tgz", size: 10 }, accept), "");
  assert.equal(isRefinableFile("资料.tar.gz", catalog), true);
  // 本机解不开的压缩包：拦截理由要指「先解压」，不能套用「转 PDF/TXT」
  const reason = refineRejectReason("资料.7z", catalog);
  assert.match(reason, /解压/);
  assert.doesNotMatch(reason, /转成 PDF/);
  // 兜底目录（接口不可用）也要有压缩包，且 rar 是灰显 + 给指引
  const fallbackArchive = refineFallbackCatalog().groups.find((item) => item.id === "archive");
  assert.ok(fallbackArchive.formats.some((item) => item.ext === ".tar.gz" && item.supported));
  assert.ok(fallbackArchive.formats.some((item) => item.ext === ".rar" && !item.supported));
});

test("refineAcceptFromCatalog 优先用接口的 refine_accept，并剔除本机不可解析的格式", () => {
  const catalog = normalizeCatalog({ ...payload(), refine_accept: ".pdf,.docx,.xlsx,.png,.heic" });
  // .heic 在 supported=false（缺解码器）→ 不进 accept，避免「可选却必然报错」
  assert.equal(refineAcceptFromCatalog(catalog), ".pdf,.docx,.xlsx,.png");
});

test("refineAcceptFromCatalog 在接口缺 refine_accept 时按目录派生（排除音视频/未知）", () => {
  const catalog = normalizeCatalog({
    groups: [
      ...payload().groups,
      group("media", "音视频", [".mp3", ".mp4"]),
      { id: "other", label: "其他", formats: [{ ext: ".bin", label: "未知", supported: false, ocr: false, note: "", group: "other" }] },
    ],
    accept: ".pdf,.docx,.xlsx,.png,.mp3,.mp4",
  });
  const accept = refineAcceptFromCatalog(catalog);
  assert.equal(accept, ".pdf,.docx,.xlsx,.png");
  assert.equal(accept.includes(".mp3"), false);
  assert.equal(accept.includes(".bin"), false);
});

test("refineFallbackCatalog / refineAcceptFallback：接口不可用时主推能力不缩水", () => {
  const catalog = refineFallbackCatalog();
  const accept = refineAcceptFromCatalog(catalog);
  for (const ext of [".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".ppt", ".pptx", ".txt", ".md", ".png", ".jpg"]) {
    assert.ok(acceptExtensions(accept).includes(ext), `兜底应包含 ${ext}`);
  }
  assert.equal(accept.includes(".mp4"), false);
  assert.equal(accept, refineAcceptFallback());
  // 兜底目录补齐了表格分组（上传页的兜底列表不含 Excel，提炼面板必须有）
  assert.ok(refineGroups(catalog.groups).some((g) => g.id === "sheet"));
  assert.equal(refineGroups(catalog.groups).some((g) => g.id === "media"), false);
  assert.deepEqual(refineGroups(null), []);
});

test("isRefinableFile：音视频/未知被前端拦下，无扩展名放行给后端嗅探", () => {
  const catalog = normalizeCatalog({
    groups: [...payload().groups, group("media", "音视频", [".mp3"])],
    refine_accept: ".pdf,.docx,.xlsx,.png",
  });
  assert.equal(isRefinableFile("第三章 讲义.PDF", catalog), true);
  assert.equal(isRefinableFile("成绩单.xlsx", catalog), true);
  assert.equal(isRefinableFile("讲座录音.mp3", catalog), false);
  assert.equal(isRefinableFile("奇怪文件.exe", catalog), false);
  assert.equal(isRefinableFile("教务导出", catalog), true);   // 无扩展名 → 交给后端按内容判定
});

test("refineSummaryText：剔除音视频后按分组给出一句话摘要", () => {
  const catalog = normalizeCatalog({
    groups: [...payload().groups, group("media", "音视频", [".mp3"])],
  });
  const text = refineSummaryText(catalog.groups);
  assert.match(text, /支持 \d+ 种格式/);
  assert.ok(text.includes("文档") && text.includes("表格"));
  assert.equal(text.includes("音视频"), false);
  assert.equal(refineSummaryText([]), "支持 PDF / Word / Excel / PPT / 图片等文件");
});

test("refineRejectReason：三类拦截原因分开说（缺解码器 / 音视频 / 认不出）", () => {
  const catalog = normalizeCatalog({
    groups: [
      {
        id: "document",
        label: "文档",
        formats: [{ ext: ".pdf", label: "PDF 文档", supported: true, ocr: false, note: "扫描版自动转 OCR", group: "document" }],
      },
      {
        id: "image",
        label: "图片（OCR 识别）",
        formats: [{
          ext: ".heic", label: "HEIC 图片", supported: false, ocr: true,
          note: "离线 OCR 识别；本机若缺 HEIC 解码器请先转成 JPG/PNG", group: "image",
        }],
      },
      {
        id: "media",
        label: "音视频",
        formats: [{ ext: ".mp3", label: "MP3 音频", supported: false, ocr: false, note: "本地不联网转写，请上传课件/讲义或录音文稿", group: "media" }],
      },
    ],
    refine_accept: ".pdf",
  });
  assert.equal(refineRejectReason("第三章.pdf", catalog), "");      // 可提炼 → 无理由
  assert.equal(refineRejectReason("教务导出", catalog), "");        // 无扩展名 → 交给后端嗅探
  assert.match(refineRejectReason("板书.heic", catalog), /HEIC|解码器|JPG/);
  assert.match(refineRejectReason("讲座.mp3", catalog), /离线转写/);
  assert.match(refineRejectReason("奇怪文件.exe", catalog), /暂不支持提炼 \.exe/);
  assert.equal(findFormat(catalog, "PDF")?.label, "PDF 文档");
  assert.equal(findFormat(catalog, ".exe"), null);
});
