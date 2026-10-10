import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  CalendarDays,
  CalendarPlus,
  GraduationCap,
  HelpCircle,
  Plus,
  Trash2,
} from "lucide-react";
import { apiCourses, apiMisc } from "../api";
import { useApp } from "../App";
import type { Course } from "../types";
import { Spinner } from "../components/ui";
import AIConfigCard from "../components/AIConfigCard";
import AuthCard from "../components/AuthCard";
import ExportCard from "../components/ExportCard";
import PushCard from "../components/PushCard";
import SyncCard from "../components/SyncCard";
import TimetableImport from "../components/TimetableImport";
import WallpaperPicker from "../components/WallpaperPicker";

export default function Settings() {
  const { bump, llmStatus, openOnboarding } = useApp();
  const [courses, setCourses] = useState<Course[]>([]);
  const [semesterStart, setSemesterStart] = useState("");
  const [newCourse, setNewCourse] = useState({ name: "", semester: "", start_date: "2026-08-24" });
  const [saving, setSaving] = useState(false);
  const [health, setHealth] = useState<{ ocr: string; llm: string } | null>(null);
  const [showImport, setShowImport] = useState(false);

  useEffect(() => {
    apiCourses.list().then(setCourses).catch(console.error);
    apiMisc.getSettings().then((s) => setSemesterStart(s.semester_start ?? "")).catch(console.error);
    apiMisc.health().then(setHealth).catch(console.error);
  }, []);

  const addCourse = async () => {
    if (!newCourse.name.trim()) return;
    setSaving(true);
    try {
      await apiCourses.create({
        name: newCourse.name.trim(),
        semester: newCourse.semester.trim(),
        start_date: newCourse.start_date || null,
      });
      setNewCourse({ name: "", semester: "", start_date: "2026-08-24" });
      setCourses(await apiCourses.list());
      bump();
    } finally {
      setSaving(false);
    }
  };

  const removeCourse = async (c: Course) => {
    if (!confirm(`删除课程「${c.name}」？其资料和任务不会被删除。`)) return;
    await apiCourses.remove(c.id);
    setCourses(await apiCourses.list());
    bump();
  };

  const saveSemester = async () => {
    await apiMisc.setSetting("semester_start", semesterStart);
    alert("已保存。第N周 类型的时间表达将按该日期计算。");
  };

  return (
    <div className="mobile-page space-y-6">
      <div className="mobile-page-heading flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-slate-800">设置</h1>
          <p className="mt-1 text-sm text-slate-500">课程管理 · 学期配置 · 账号安全 · AI 接入 · 多端同步 · 数据导出 · 系统状态</p>
        </div>
        <button
          onClick={openOnboarding}
          className="flex shrink-0 items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-500 transition hover:border-brand-200 hover:text-brand-600"
        >
          <HelpCircle size={15} />
          新手引导
        </button>
      </div>

      {/* 背景壁纸（动态壁纸选择，本地保存） */}
      <WallpaperPicker />

      {/* AI 模型接入 */}
      <AIConfigCard />

      {/* 账号安全 · 登录保护（本地账号体系） */}
      <AuthCard />

      {/* 多端同步 */}
      <SyncCard />

      {/* 课程管理 */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
            <GraduationCap size={16} className="text-brand-500" />
            我的课程
          </div>
          <div className="flex items-center gap-2">
            <Link
              to="/timetable"
              className="flex items-center gap-1.5 rounded-xl border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:border-brand-300 hover:text-brand-600"
            >
              <CalendarDays size={14} />
              查看周课表
            </Link>
            <button
              onClick={() => setShowImport(true)}
              className="flex items-center gap-1.5 rounded-xl border border-brand-200 bg-brand-50 px-3 py-1.5 text-xs font-medium text-brand-700 transition hover:border-brand-400"
            >
              <CalendarPlus size={14} />
              导入课表
            </button>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {courses.map((c) => (
            <span
              key={c.id}
              className="group flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm ring-1 ring-slate-200"
              title={c.teacher ? `任课老师：${c.teacher}` : ""}
            >
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: c.color }} />
              {c.name}
              {c.teacher && <span className="text-[10px] text-slate-400">{c.teacher}</span>}
              {c.semester && <span className="text-[10px] text-slate-400">{c.semester}</span>}
              <button
                className="hidden text-slate-300 hover:text-red-500 group-hover:block"
                onClick={() => removeCourse(c)}
              >
                <Trash2 size={12} />
              </button>
            </span>
          ))}
          {courses.length === 0 && <span className="text-sm text-slate-400">还没有课程，可点右上角「导入课表」一键导入</span>}
        </div>
        <div className="mt-4 grid gap-2 md:grid-cols-3">
          <input
            placeholder="课程名，如：操作系统"
            className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
            value={newCourse.name}
            onChange={(e) => setNewCourse({ ...newCourse, name: e.target.value })}
          />
          <input
            placeholder="学期，如：2026-2027-1"
            className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
            value={newCourse.semester}
            onChange={(e) => setNewCourse({ ...newCourse, semester: e.target.value })}
          />
          <input
            type="date"
            placeholder="开学日期（用于第N周解析）"
            className="rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-500"
            value={newCourse.start_date}
            onChange={(e) => setNewCourse({ ...newCourse, start_date: e.target.value })}
          />
        </div>
        <button
          onClick={addCourse}
          disabled={saving || !newCourse.name.trim()}
          className="mt-3 flex items-center gap-1.5 rounded-xl bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
        >
          {saving ? <Spinner /> : <Plus size={15} />} 添加课程
        </button>
        <p className="mt-2 text-[11px] text-slate-400">
          填写开学日期后，支持「第12周周三」这类时间表达自动换算成具体日期
        </p>
      </div>

      {/* 学期全局设置 */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-3 text-sm font-semibold text-slate-700">学期起始日期（全局）</div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="date"
            className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
            value={semesterStart}
            onChange={(e) => setSemesterStart(e.target.value)}
          />
          <button
            onClick={saveSemester}
            className="rounded-xl bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
          >
            保存
          </button>
          <span className="text-[11px] text-slate-400">
            课程未配置开学日期时使用此日期作为「第N周」的锚点
          </span>
        </div>
      </div>

      {/* 数据导出（支持增量导出 + 导出历史） */}
      <ExportCard />

      {/* DDL 提醒：真正的 Web Push（Service Worker push 订阅，关页也能收）+ iCal 兜底 */}
      <PushCard />

      {/* 系统状态 */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 text-xs text-slate-500 shadow-sm">
        <div className="mb-2 text-sm font-semibold text-slate-700">系统状态</div>
        <ul className="space-y-1">
          <li>· AI Agent：{health ? (health.llm.includes("offline") ? "规则引擎模式（离线可用，在上方「AI 模型接入」配置后启用 LLM）" : "LLM 已连接") : "检测中…"}</li>
          <li>· OCR：{health ? (health.ocr === "available" ? "可用（截图/拍照识图）" : health.ocr === "loading" ? "模型加载中（首次启动需几秒，稍后自动就绪）" : "未启用（文本/文件通道不受影响）") : "检测中…"}</li>
          <li>· 数据存储：本地 SQLite + 文件目录（backend/data），不依赖任何云服务</li>
          <li>· 多端同步：点对点直连（上方配置），支持完整备份 zip 导出/恢复；同步令牌默认掩码显示，明文需在本机显式点击查看</li>
          <li>· 导出：支持增量导出（只导出自上次以来的变更）与导出历史，导出记录同时作为增量基线</li>
          <li>· 提醒：Web Push（浏览器推送服务 → Service Worker，页面关闭也能收到）+ 站内角标；iCal 导出可同步手机日历兜底</li>
        </ul>
        {llmStatus.startsWith("offline") && (
          <div className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-amber-700">
            当前未配置 LLM API Key。规则引擎已可完整演示；在「AI 模型接入」卡片里填入
            DeepSeek / OpenAI 兼容接口（或本地 Ollama）即可升级为「规则 + LLM 双引擎」。
          </div>
        )}
      </div>

      {/* 导入课表：iCal / JSON → 课程 + 课表时段 */}
      {showImport && (
        <TimetableImport
          onClose={() => setShowImport(false)}
          onImported={async () => {
            setCourses(await apiCourses.list());
            bump();
          }}
        />
      )}
    </div>
  );
}
