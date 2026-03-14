const PAGE_STATE_KEY = "video_translater.source_page_state.v1";
const PROJECT_POLL_INTERVAL_MS = 2000;
const CLIP_TASK_POLL_INTERVAL_MS = 350;

const DEFAULT_PAGE_STATE = {
  subtitleSync: true,
  sourceSegmentsScrollTop: 0,
  targetSegmentsScrollTop: 0,
  sourceVolume: 100,
  targetVolume: 100,
  sourceSpeed: "1",
  targetSpeed: "1",
  targetLanguage: "English",
  translatorBackend: "codex-medium",
  ttsModel: "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
  segmentDrafts: {},
  activeEditor: null,
};

const REVIEW_FINAL_STATUSES = new Set(["accepted", "rejected", "customized", "unchanged", "failed"]);
const REVIEW_MUTABLE_STATUSES = new Set(["pending"]);
const TARGET_LANGUAGE_OPTIONS = [
  { value: "Chinese", label: "中文" },
  { value: "English", label: "英语" },
  { value: "French", label: "法语" },
  { value: "German", label: "德语" },
  { value: "Italian", label: "意大利语" },
  { value: "Japanese", label: "日语" },
  { value: "Korean", label: "韩语" },
  { value: "Portuguese", label: "葡萄牙语" },
  { value: "Russian", label: "俄语" },
  { value: "Spanish", label: "西班牙语" },
];

const state = {
  systemStatus: null,
  projects: [],
  currentProjectId: null,
  detail: null,
  sidebarState: {
    selected_project_id: null,
    project_list_scroll_top: 0,
    source_review_visibility_by_project: {},
  },
  pageState: loadLocalPageState(),
  pollTimer: null,
  projectContextProjectId: null,
  textContextTarget: null,
  clipDialogProjectId: null,
  clipTask: null,
  clipPollTimer: null,
  bulkPendingEnabled: false,
  currentClipAudio: null,
  currentClipAudioSide: "source",
  renderedModelSettingsKey: null,
  isSyncingScroll: false,
  scrollLockHandle: null,
  scrollSyncFrame: null,
  pendingScrollLeader: null,
  scrollPersistTimer: null,
  scrollGestureLeader: null,
  scrollGestureTimer: null,
  suppressedScrollEvents: {
    source: [],
    target: [],
  },
  segmentPlayback: {
    source: null,
    target: null,
  },
  activeSegmentIds: {
    source: null,
    target: null,
  },
  playbackLeader: null,
  isPageUnloading: false,
};

const sourceVideo = document.getElementById("source-video");
const targetVideo = document.getElementById("target-video");
const subtitleSyncToggle = document.getElementById("subtitle-sync");
const sourceLanguageSelect = document.getElementById("source-language");
const targetLanguageSelect = document.getElementById("target-language");
const sourceVolumeSlider = document.getElementById("source-volume");
const targetVolumeSlider = document.getElementById("target-volume");
const sourceSpeedSelect = document.querySelector(".speed-select[data-player='source']");
const targetSpeedSelect = document.querySelector(".speed-select[data-player='target']");
const projectList = document.getElementById("project-list");
const modelSettings = document.getElementById("model-settings");
const sourceSegments = document.getElementById("source-segments");
const targetSegments = document.getElementById("target-segments");
const fileInput = document.getElementById("file-input");
const startSourceProcessButton = document.getElementById("start-source-process");
const startFullProcessButton = document.getElementById("start-full-process");
const startTargetProcessButton = document.getElementById("start-target-process");
const projectContextMenu = document.getElementById("project-context-menu");
const textContextMenu = document.getElementById("text-context-menu");
const clipDialog = document.getElementById("clip-dialog");
const clipForm = document.getElementById("clip-form");
const clipStartInput = document.getElementById("clip-start");
const clipEndInput = document.getElementById("clip-end");
const clipError = document.getElementById("clip-error");
const clipCancelButton = document.getElementById("clip-cancel");
const clipSubmitButton = document.getElementById("clip-submit");
const clipProgress = document.getElementById("clip-progress");
const clipProgressStage = document.getElementById("clip-progress-stage");
const clipProgressPercent = document.getElementById("clip-progress-percent");
const clipProgressFill = document.getElementById("clip-progress-fill");
const clipProgressDetail = document.getElementById("clip-progress-detail");
const jobStage = document.getElementById("job-stage");
const jobMessage = document.getElementById("job-message");
const jobStep = document.getElementById("job-step");
const jobProgressFill = document.getElementById("job-progress-fill");
const jobStatusRail = document.getElementById("job-status-rail");
const jobStageProgress = document.getElementById("job-stage-progress");
const jobElapsed = document.getElementById("job-elapsed");
const jobEta = document.getElementById("job-eta");

function sourceMediaVersion(detail) {
  if (!detail?.paths?.source_video) {
    return "";
  }
  return [
    detail.manifest?.updated_at || "",
    detail.source_segments?.segments?.length || 0,
    detail.paths.source_video,
  ].join(":");
}

function loadLocalPageState() {
  try {
    const raw = window.localStorage.getItem(PAGE_STATE_KEY);
    if (!raw) {
      return { ...DEFAULT_PAGE_STATE };
    }
    const parsed = JSON.parse(raw);
    const parsedDrafts = parsed.segmentDrafts && typeof parsed.segmentDrafts === "object" ? parsed.segmentDrafts : {};
    const parsedEditor = parsed.activeEditor && typeof parsed.activeEditor === "object" ? parsed.activeEditor : null;
    return {
      subtitleSync: parsed.subtitleSync !== false,
      sourceSegmentsScrollTop: Number.isFinite(Number(parsed.sourceSegmentsScrollTop))
        ? Number(parsed.sourceSegmentsScrollTop)
        : DEFAULT_PAGE_STATE.sourceSegmentsScrollTop,
      targetSegmentsScrollTop: Number.isFinite(Number(parsed.targetSegmentsScrollTop))
        ? Number(parsed.targetSegmentsScrollTop)
        : DEFAULT_PAGE_STATE.targetSegmentsScrollTop,
      sourceVolume: Number.isFinite(Number(parsed.sourceVolume)) ? Number(parsed.sourceVolume) : DEFAULT_PAGE_STATE.sourceVolume,
      targetVolume: Number.isFinite(Number(parsed.targetVolume)) ? Number(parsed.targetVolume) : DEFAULT_PAGE_STATE.targetVolume,
      sourceSpeed: typeof parsed.sourceSpeed === "string" ? parsed.sourceSpeed : DEFAULT_PAGE_STATE.sourceSpeed,
      targetSpeed: typeof parsed.targetSpeed === "string" ? parsed.targetSpeed : DEFAULT_PAGE_STATE.targetSpeed,
      targetLanguage: typeof parsed.targetLanguage === "string" ? parsed.targetLanguage : DEFAULT_PAGE_STATE.targetLanguage,
      translatorBackend: typeof parsed.translatorBackend === "string" ? parsed.translatorBackend : DEFAULT_PAGE_STATE.translatorBackend,
      ttsModel:
        typeof parsed.ttsModel === "string" && parsed.ttsModel !== "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"
          ? parsed.ttsModel
          : DEFAULT_PAGE_STATE.ttsModel,
      segmentDrafts: parsedDrafts,
      activeEditor: parsedEditor,
    };
  } catch (_error) {
    return { ...DEFAULT_PAGE_STATE };
  }
}

function writeLocalPageState(patch) {
  state.pageState = { ...state.pageState, ...patch };
  window.localStorage.setItem(PAGE_STATE_KEY, JSON.stringify(state.pageState));
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    cache: "no-store",
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  if (!response.ok) {
    let detail = "";
    try {
      if (contentType.includes("application/json")) {
        const payload = await response.json();
        detail = String(payload.detail || payload.error || "");
      } else {
        detail = (await response.text()).trim();
      }
    } catch (_error) {
      detail = "";
    }
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response;
}

function clipTaskRunning() {
  return Boolean(state.clipTask && ["queued", "running"].includes(state.clipTask.status));
}

function clipTaskTerminal() {
  return Boolean(state.clipTask && ["completed", "failed"].includes(state.clipTask.status));
}

function clipTaskStageLabel(task = state.clipTask) {
  const stage = String(task?.stage || "");
  const labels = {
    queued: "等待开始",
    preparing: "准备截取参数",
    extracting_audio: "提取分析音频",
    aligning_cut: "对齐结束点",
    clipping_video: "截取视频片段",
    finalizing: "写入新项目",
    completed: "截取完成",
    failed: "截取失败",
  };
  return labels[stage] || "截取片段";
}

function mediaUrl(relativePath, versionToken = "") {
  if (!relativePath || !state.currentProjectId) {
    return "";
  }
  const [root, ...rest] = String(relativePath).split("/");
  const base = `/media/${encodeURIComponent(state.currentProjectId)}/${encodeURIComponent(root)}/${rest.map(encodeURIComponent).join("/")}`;
  return versionToken ? `${base}?v=${encodeURIComponent(versionToken)}` : base;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function stemFromFilename(filename) {
  const value = String(filename || "").trim();
  return value.replace(/\.[^.]+$/, "") || "未命名项目";
}

function draftKey(projectId, side, segmentId) {
  return `${projectId || ""}::${side || ""}::${segmentId || ""}`;
}

function getSegmentDraft(projectId, side, segmentId) {
  return state.pageState.segmentDrafts?.[draftKey(projectId, side, segmentId)];
}

function setSegmentDraft(projectId, side, segmentId, value) {
  const nextDrafts = {
    ...(state.pageState.segmentDrafts || {}),
    [draftKey(projectId, side, segmentId)]: value,
  };
  writeLocalPageState({ segmentDrafts: nextDrafts });
}

function clearSegmentDraft(projectId, side, segmentId) {
  const nextDrafts = { ...(state.pageState.segmentDrafts || {}) };
  delete nextDrafts[draftKey(projectId, side, segmentId)];
  writeLocalPageState({ segmentDrafts: nextDrafts });
}

function normalizedSegmentText(value) {
  return String(value || "").trim();
}

function segmentTextChanged(currentValue, persistedValue) {
  return normalizedSegmentText(currentValue) !== normalizedSegmentText(persistedValue);
}

function segmentSaveButton(row) {
  return row?.querySelector("button.save");
}

function setSegmentSaveButtonState(row, dirty) {
  const button = segmentSaveButton(row);
  if (!button) {
    return;
  }
  button.disabled = !dirty;
  row.dataset.dirty = dirty ? "true" : "false";
}

function hasLocalDirtyDraft(projectId, side, segmentId, persistedValue) {
  const draft = getSegmentDraft(projectId, side, segmentId);
  if (draft == null) {
    return false;
  }
  return segmentTextChanged(draft, persistedValue);
}

function isReviewVisible(projectId) {
  if (!projectId) {
    return true;
  }
  const visibilityMap = state.sidebarState.source_review_visibility_by_project || {};
  return visibilityMap[projectId] !== false;
}

async function persistSidebarState(patch) {
  const nextState = { ...state.sidebarState, ...patch };
  state.sidebarState = nextState;
  state.sidebarState = await request("/api/ui/sidebar-state", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

function pendingReviewSuggestions(review) {
  return (review?.suggestions || []).filter((item) => item.status === "pending");
}

function resolvedReviewCount(review) {
  return (review?.suggestions || []).filter((item) => REVIEW_FINAL_STATUSES.has(item.status)).length;
}

function reviewSuggestionMap(review) {
  const map = new Map();
  for (const suggestion of review?.suggestions || []) {
    map.set(suggestion.segment_id, suggestion);
  }
  return map;
}

function formatClock(seconds) {
  if (!Number.isFinite(Number(seconds))) {
    return "--";
  }
  const total = Math.max(0, Number(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = Math.floor(total % 60);
  const millis = Math.round((total - Math.floor(total)) * 1000);
  const time = `${String(minutes).padStart(hours > 0 ? 2 : 1, "0")}:${String(secs).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
  return hours > 0 ? `${String(hours).padStart(2, "0")}:${time}` : time;
}

function formatElapsed(seconds) {
  if (!Number.isFinite(Number(seconds))) {
    return "--";
  }
  const total = Math.max(0, Math.floor(Number(seconds)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) {
    return `${hours}h ${minutes}m ${secs}s`;
  }
  if (minutes > 0) {
    return `${minutes}m ${secs}s`;
  }
  return `${secs}s`;
}

function parseFractionFromText(text, unit) {
  const match = String(text || "").match(new RegExp(String.raw`(\d+)\s*\/\s*(\d+)\s*${unit}`));
  if (!match) {
    return null;
  }
  return {
    completed: Number(match[1]),
    total: Number(match[2]),
  };
}

function statusTextForProject(project) {
  const status = String(project.effective_status || project.status || "idle");
  const labels = {
    idle: "空闲",
    extracting_audio: "提取音频",
    transcribing: "转写中",
    reviewing: "纠错中",
    translating_target: "翻译合成中",
    synthesizing_target: "重新合成中",
    aligning_target: "时序对齐中",
    rendering_target_video: "生成视频中",
    source_review: "待确认",
    source_ready: "已转写",
    target_ready: "已合成",
    edited: "已编辑",
    error: "失败",
    complete: "已完成",
  };
  return labels[status] || status;
}

function chineseJobMessage(rawMessage) {
  const text = String(rawMessage || "").trim();
  if (!text) {
    return "就绪";
  }
  const mappings = [
    [/^Extracting source audio\.$/, "正在提取源音频"],
    [/^Generating source subtitles\.$/, "正在生成原始转写"],
    [/^Preparing MLX ASR transcription \((\d+) chunks\)\.$/, "正在准备转写，共 $1 个语音块"],
    [/^Running MLX ASR recognition \((\d+)\/(\d+) chunks\)\.?/, "正在识别第 $1/$2 个语音块"],
    [/^Preparing source clips \((\d+)\/(\d+)\)\.?$/, "正在切出第 $1/$2 段音频"],
    [/^Reviewing source subtitles with Codex\.$/, "正在生成 Codex 纠错建议"],
    [/^Correcting source subtitles \((\d+)\/(\d+) segments\)\.?$/, "正在纠错第 $1/$2 段"],
    [/^Source corrections are ready for review \((\d+) pending\)\.$/, "Codex 纠错建议已生成，待确认 $1 条"],
    [/^Source correction review finished\.$/, "Codex 纠错已完成"],
    [/^Source subtitles finished\.$/, "转写分段已完成"],
    [/^Source subtitles finished\. (\d+) Codex correction suggestions are ready\.$/, "转写分段已完成，已有 $1 条 Codex 纠错建议"],
    [/^Translating and synthesizing target segments \((\d+)\/(\d+) segments\)\.?$/, "正在翻译并合成第 $1/$2 段"],
    [/^Re-synthesizing target segments \((\d+)\/(\d+) segments\)\.?$/, "正在重新合成第 $1/$2 段"],
    [/^Re-synthesizing target audio\.$/, "正在重新合成语音"],
    [/^Aligning target timing \((\d+)\/(\d+) segments\)\.?$/, "正在对齐第 $1/$2 段"],
    [/^Rendering target video\.$/, "正在生成目标视频"],
    [/^Rendering target video \(([0-9.]+)\/([0-9.]+)s\)\.?$/, "正在生成目标视频（$1/$2s）"],
    [/^Target video rendering finished\.$/, "目标视频生成完成"],
    [/^Target translation and synthesis finished\.$/, "翻译合成已完成"],
    [/^Target translation and synthesis failed\.$/, "翻译合成失败"],
    [/^Source subtitle extraction failed\.$/, "转写分段失败"],
    [/^Task stopped by user\.$/, "任务已停止"],
    [/^Task stopped because the worker exited unexpectedly\.$/, "任务意外中断"],
    [/^Ready$/, "就绪"],
  ];
  for (const [pattern, replacement] of mappings) {
    if (pattern.test(text)) {
      return text.replace(pattern, replacement);
    }
  }
  return text;
}

function truncateStatusText(rawText, maxLength = 96) {
  const compact = String(rawText || "").replace(/\s+/g, " ").trim();
  if (!compact) {
    return "";
  }
  if (compact.length <= maxLength) {
    return compact;
  }
  return `${compact.slice(0, Math.max(0, maxLength - 1)).trimEnd()}…`;
}

function summarizeJobError(rawError) {
  const text = String(rawError || "").replace(/\0/g, "").trim();
  if (!text) {
    return "请查看 worker 日志";
  }
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const meaningful = [];
  for (const line of lines) {
    if (/^Traceback\b/i.test(line)) {
      break;
    }
    if (/^File \".+\", line \d+, in /.test(line)) {
      continue;
    }
    meaningful.push(line.replace(/^[A-Za-z_]+Error:\s*/, ""));
  }
  const summary = meaningful[0] || lines[0].replace(/^[A-Za-z_]+Error:\s*/, "") || "请查看 worker 日志";
  return truncateStatusText(chineseJobMessage(summary), 110);
}

function deriveJobPresentation(detail) {
  const empty = {
    stageLine: "空闲（0/0）",
    summaryLine: detail ? "就绪" : "暂无项目",
    detailLine: detail ? "等待任务开始" : "请先导入视频",
    summaryTitle: detail ? "就绪" : "暂无项目",
    detailTitle: detail ? "等待任务开始" : "请先导入视频",
    stageProgressLine: "本级 0/0",
    progressPercent: 0,
    elapsedLine: "--",
    etaLine: "--",
    tone: "idle",
  };
  if (!detail) {
    return empty;
  }

  const { manifest, job, source_segments: sourceDocument, source_correction_review: review } = detail;
  const segmentCount = sourceDocument?.segments?.length || 0;
  const targetDraftCount = detail.target_segments_draft?.segments?.length || 0;
  const targetAlignedCount = detail.target_segments_aligned?.segments?.length || 0;
  const pendingCount = pendingReviewSuggestions(review).length;
  const resolvedCount = resolvedReviewCount(review);
  const totalSuggestions = review?.suggestions?.length || 0;

  if (job.running) {
    const rawMessage = String(job.message || "");
    const chunkProgress = parseFractionFromText(rawMessage, "chunks");
    const reviewProgress = parseFractionFromText(rawMessage, "segments");
    let stageLine = "处理中（0/0）";
    let stageProgressLine = "本级 0/0";
    if (job.stage === "extracting_audio") {
      const completed = job.stage_ratio >= 1 ? 1 : 0;
      stageLine = `正在提取音频（${completed}/1）`;
      stageProgressLine = `本级 ${completed}/1`;
    } else if (job.stage === "transcribing") {
      const completed = chunkProgress?.completed ?? 0;
      const total = chunkProgress?.total ?? 0;
      stageLine = `正在转写（${completed}/${total}）`;
      stageProgressLine = `本级 ${completed}/${total}`;
    } else if (job.stage === "reviewing") {
      const completed = reviewProgress?.completed ?? review?.completed_segments ?? 0;
      const total = reviewProgress?.total ?? review?.total_segments ?? 0;
      stageLine = `正在纠错（${completed}/${total}）`;
      stageProgressLine = `本级 ${completed}/${total}`;
    } else if (job.stage === "translating_target" || job.stage === "synthesizing_target") {
      const completed = Math.max(0, Math.round(Number(job.items_completed) || targetDraftCount || 0));
      const total = Math.max(completed, Math.round(Number(job.items_total) || segmentCount || 0));
      stageLine = job.stage === "synthesizing_target"
        ? `正在重新合成（${completed}/${total}）`
        : `正在翻译合成（${completed}/${total}）`;
      stageProgressLine = `本级 ${completed}/${total}`;
    } else if (job.stage === "aligning_target") {
      const completed = Math.max(0, Math.round(Number(job.items_completed) || targetAlignedCount || 0));
      const total = Math.max(completed, Math.round(Number(job.items_total) || targetDraftCount || segmentCount || 0));
      stageLine = `正在时序对齐（${completed}/${total}）`;
      stageProgressLine = `本级 ${completed}/${total}`;
    } else if (job.stage === "rendering_target_video") {
      const ratio = Math.max(0, Math.min(100, Math.round((job.stage_ratio || 0) * 100)));
      stageLine = `正在生成视频（${ratio}/100）`;
      stageProgressLine = `本级 ${ratio}/100`;
    }
    return {
      stageLine,
      summaryLine: chineseJobMessage(rawMessage) || "任务运行中",
      detailLine: chineseJobMessage(job.step_label || rawMessage || "处理中"),
      summaryTitle: String(rawMessage || "任务运行中"),
      detailTitle: String(job.step_label || rawMessage || "处理中"),
      stageProgressLine,
      progressPercent: Math.max(0, Math.min(100, Math.round((job.overall_ratio || 0) * 100))),
      elapsedLine: formatElapsed(job.elapsed_seconds),
      etaLine: formatElapsed(job.eta_seconds),
      tone: job.stage === "reviewing" ? "reviewing" : "running",
    };
  }

  if ((manifest.status === "source_review" || pendingCount > 0) && totalSuggestions > 0) {
    const ratio = totalSuggestions > 0 ? Math.round((resolvedCount / totalSuggestions) * 100) : 0;
    return {
      stageLine: `待确认（${resolvedCount}/${totalSuggestions}）`,
      summaryLine: "Codex 纠错建议已生成",
      detailLine: `还有 ${pendingCount} 条待处理，接受、拒绝或直接改正文后保存即可`,
      summaryTitle: "Codex 纠错建议已生成",
      detailTitle: `还有 ${pendingCount} 条待处理，接受、拒绝或直接改正文后保存即可`,
      stageProgressLine: `本级 ${resolvedCount}/${totalSuggestions}`,
      progressPercent: ratio,
      elapsedLine: "--",
      etaLine: "--",
      tone: "waiting",
    };
  }

  if (manifest.status === "error" || job.error) {
    const detailSummary = job.error || summarizeJobError(job.error_detail);
    const detailTitle = String(job.error_detail || job.error || "请查看 worker 日志");
    return {
      stageLine: `失败（0/0）`,
      summaryLine: chineseJobMessage(job.message || "Source subtitle extraction failed."),
      detailLine: detailSummary || "请查看 worker 日志",
      summaryTitle: String(job.message || "Source subtitle extraction failed."),
      detailTitle,
      stageProgressLine: "本级 0/0",
      progressPercent: 0,
      elapsedLine: formatElapsed(job.elapsed_seconds),
      etaLine: "--",
      tone: "error",
    };
  }

  if (manifest.status === "target_ready" && targetAlignedCount > 0) {
    return {
      stageLine: `已完成（${targetAlignedCount}/${targetAlignedCount}）`,
      summaryLine: "翻译合成已完成",
      detailLine: `共生成 ${targetAlignedCount} 段 target 文本`,
      summaryTitle: "翻译合成已完成",
      detailTitle: `共生成 ${targetAlignedCount} 段 target 文本`,
      stageProgressLine: `本级 ${targetAlignedCount}/${targetAlignedCount}`,
      progressPercent: 100,
      elapsedLine: formatElapsed(job.elapsed_seconds),
      etaLine: "0s",
      tone: "complete",
    };
  }

  if (segmentCount > 0) {
    return {
      stageLine: `已完成（${segmentCount}/${segmentCount}）`,
      summaryLine: "转写分段已完成",
      detailLine: pendingCount > 0 ? `已有 ${pendingCount} 条纠错建议待确认` : `共生成 ${segmentCount} 段 source 文本`,
      summaryTitle: "转写分段已完成",
      detailTitle: pendingCount > 0 ? `已有 ${pendingCount} 条纠错建议待确认` : `共生成 ${segmentCount} 段 source 文本`,
      stageProgressLine: `本级 ${segmentCount}/${segmentCount}`,
      progressPercent: 100,
      elapsedLine: formatElapsed(job.elapsed_seconds),
      etaLine: "0s",
      tone: "complete",
    };
  }

  return empty;
}

function syncPlaybackSettings() {
  const sourceVolume = Math.max(0, Math.min(1, state.pageState.sourceVolume / 100));
  sourceVideo.volume = sourceVolume;
  sourceVideo.playbackRate = Number(state.pageState.sourceSpeed || "1");
  if (targetVideo) {
    const fallbackVisual = targetVideoUsesSourceVisual();
    targetVideo.muted = fallbackVisual;
    targetVideo.volume = fallbackVisual ? 0 : Math.max(0, Math.min(1, state.pageState.targetVolume / 100));
    targetVideo.playbackRate = Number(state.pageState.targetSpeed || "1");
  }
  if (state.currentClipAudio) {
    const isTargetClip = state.currentClipAudioSide === "target";
    state.currentClipAudio.volume = Math.max(
      0,
      Math.min(1, (isTargetClip ? state.pageState.targetVolume : state.pageState.sourceVolume) / 100)
    );
    state.currentClipAudio.playbackRate = Number(isTargetClip ? state.pageState.targetSpeed || "1" : state.pageState.sourceSpeed || "1");
  }
}

function stopCurrentClipAudio() {
  if (!state.currentClipAudio) {
    return;
  }
  state.currentClipAudio.pause();
  state.currentClipAudio.currentTime = 0;
  state.currentClipAudio = null;
  state.currentClipAudioSide = "source";
}

function pauseCurrentClipAudio(side = null) {
  if (!state.currentClipAudio) {
    return;
  }
  if (side && state.currentClipAudioSide !== side) {
    return;
  }
  state.currentClipAudio.pause();
}

function playCurrentClipAudio(side = null) {
  if (!state.currentClipAudio) {
    return Promise.resolve();
  }
  if (side && state.currentClipAudioSide !== side) {
    return Promise.resolve();
  }
  syncPlaybackSettings();
  return state.currentClipAudio.play().catch(() => {});
}

function setSourceVideo(url) {
  if ((sourceVideo.getAttribute("src") || "") === url) {
    return;
  }
  sourceVideo.pause();
  sourceVideo.removeAttribute("src");
  if (url) {
    sourceVideo.setAttribute("src", url);
  }
  sourceVideo.load();
  syncPlaybackSettings();
}

function setTargetVideo(url) {
  if (!targetVideo) {
    return;
  }
  const currentSrc = targetVideo.getAttribute("src") || "";
  const shouldForceReload = Boolean(url && targetVideo.error);
  if (currentSrc === url && !shouldForceReload) {
    return;
  }
  targetVideo.pause();
  targetVideo.removeAttribute("src");
  if (url) {
    targetVideo.setAttribute("src", url);
  }
  targetVideo.load();
  syncPlaybackSettings();
}

function restoreSegmentScrollState() {
  const sourceTop = Number(state.pageState.sourceSegmentsScrollTop || 0);
  const targetTop = Number(state.pageState.targetSegmentsScrollTop || 0);
  const apply = () => {
    sourceSegments.scrollTop = sourceTop;
    targetSegments.scrollTop = targetTop;
  };
  apply();
  window.requestAnimationFrame(apply);
}

function targetMediaVersion(detail) {
  if (!detail) {
    return "";
  }
  const targetPath = detail.paths?.target_video || detail.paths?.target_track || "";
  if (!targetPath) {
    return "";
  }
  return [
    detail.manifest?.updated_at || "",
    detail.target_segments_aligned?.segments?.length || 0,
    detail.target_segments_draft?.segments?.length || 0,
    targetPath,
  ].join(":");
}

function targetVideoUsesSourceVisual(detail = state.detail) {
  return Boolean(detail?.paths?.source_video && !detail?.paths?.target_video);
}

function syncSegmentRowHeights() {
  const sourceRows = Array.from(sourceSegments.querySelectorAll(".segment-row"));
  const targetRows = Array.from(targetSegments.querySelectorAll(".segment-row"));
  for (const row of [...sourceRows, ...targetRows]) {
    row.style.minHeight = "";
    row.style.height = "";
  }
  const targetById = new Map(targetRows.map((row) => [row.dataset.segmentId, row]));
  const pairedIds = new Set();
  for (const sourceRow of sourceRows) {
    const segmentId = sourceRow.dataset.segmentId;
    const targetRow = targetById.get(segmentId);
    const sourceHeight = Math.ceil(sourceRow.scrollHeight);
    const targetHeight = targetRow ? Math.ceil(targetRow.scrollHeight) : 0;
    const resolvedHeight = Math.max(sourceHeight, targetHeight);
    if (resolvedHeight > 0) {
      sourceRow.style.minHeight = `${resolvedHeight}px`;
      sourceRow.style.height = `${resolvedHeight}px`;
      if (targetRow) {
        targetRow.style.minHeight = `${resolvedHeight}px`;
        targetRow.style.height = `${resolvedHeight}px`;
        pairedIds.add(segmentId);
      }
    }
  }
  for (const targetRow of targetRows) {
    if (pairedIds.has(targetRow.dataset.segmentId)) {
      continue;
    }
    const resolvedHeight = Math.ceil(targetRow.scrollHeight);
    if (resolvedHeight > 0) {
      targetRow.style.minHeight = `${resolvedHeight}px`;
      targetRow.style.height = `${resolvedHeight}px`;
    }
  }
}

function renderModelSettings() {
  if (!state.systemStatus) {
    if (!modelSettings.innerHTML) {
      modelSettings.innerHTML = "<p class='muted'>模型设置加载中。</p>";
    }
    return;
  }
  const options = state.systemStatus.options || {};
  const settings = state.systemStatus.settings || {};
  const clipping = clipTaskRunning();
  const providerByRole = new Map((state.systemStatus?.providers || []).map((item) => [item.role, item]));
  const items = [
    {
      key: "asr_model",
      label: "语音转写",
      options: options.asr_model || [],
      note: providerByRole.get("transcriber")?.reason || "",
      value: settings.asr_model,
    },
    {
      key: "tts_model",
      label: "语音合成",
      options: options.tts_model || [],
      note: providerByRole.get("synthesizer")?.reason || "",
      value: settings.tts_model || DEFAULT_PAGE_STATE.ttsModel,
    },
    {
      key: "review_backend",
      label: "文本校译",
      options: options.review_backend || [],
      note: providerByRole.get("reviewer")?.reason || "",
      value: settings.review_backend || DEFAULT_PAGE_STATE.translatorBackend,
    },
  ];
  const renderKey = JSON.stringify(
    items.map((item) => ({
      key: item.key,
      value: item.value,
      note: item.note,
      options: (item.options || []).map((option) => [option.value, option.label]),
      clipping,
    }))
  );
  if (state.renderedModelSettingsKey === renderKey) {
    return;
  }
  state.renderedModelSettingsKey = renderKey;
  modelSettings.innerHTML = items
    .map(
      (item) => `
        <label class="setting-field">
          <span>${escapeHtml(item.label)}</span>
          <span class="select-shell"${item.note ? ` title="${escapeHtml(item.note)}"` : ""}>
            <select data-setting-key="${escapeHtml(item.key)}" aria-label="${escapeHtml(item.label)}"${item.note ? ` title="${escapeHtml(item.note)}"` : ""}${clipping ? " disabled" : ""}>
              ${(item.options || [])
                .map((option) => {
                  const selected = item.value === option.value ? " selected" : "";
                  return `<option value="${escapeHtml(option.value)}"${selected}>${escapeHtml(option.label)}</option>`;
                })
                .join("")}
            </select>
          </span>
        </label>
      `
    )
    .join("");
}

function renderProjectList() {
  const clipping = clipTaskRunning();
  if (!state.projects.length) {
    projectList.classList.toggle("disabled", clipping);
    projectList.innerHTML = "<p class='muted'>暂无项目。</p>";
    state.projectContextProjectId = null;
    projectContextMenu.hidden = true;
    return;
  }
  projectList.classList.toggle("disabled", clipping);
  projectList.innerHTML = [...state.projects]
    .reverse()
    .map((project) => {
      const active = project.id === state.currentProjectId ? "active" : "";
      const contextOpen = project.id === state.projectContextProjectId ? "context-open" : "";
      const disabled = clipping ? "disabled" : "";
      const displayStatus = statusTextForProject(project);
      const progressText = project.job_running ? ` ${Math.max(0, Math.min(Number(project.job_progress) || 0, 100))}%` : "";
      const updatedText = project.updated_at ? new Date(project.updated_at).toLocaleString() : "";
      return `
        <article class="project-card ${active} ${contextOpen} ${disabled}" data-project-id="${escapeHtml(project.id)}">
          <h4>${escapeHtml(project.name)}</h4>
          <p>${escapeHtml(displayStatus + progressText)}${updatedText ? ` · ${escapeHtml(updatedText)}` : ""}</p>
        </article>
      `;
    })
    .join("");
  projectList.querySelectorAll("[data-project-id]").forEach((node) => {
    node.addEventListener("click", async () => {
      if (clipTaskRunning()) {
        return;
      }
      await selectProject(node.dataset.projectId);
    });
    node.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      if (clipTaskRunning()) {
        return;
      }
      showProjectContextMenu(node.dataset.projectId, event.clientX, event.clientY);
    });
  });
  projectList.scrollTop = Number(state.sidebarState.project_list_scroll_top || 0);
}

function renderSourceLanguageSelect() {
  const options = state.systemStatus?.options?.source_language || [];
  const value = state.detail?.manifest?.source_language || "Auto";
  sourceLanguageSelect.innerHTML = options
    .map(
      (option) => `
        <option value="${escapeHtml(option.value)}"${option.value === value ? " selected" : ""}>
          ${escapeHtml(option.label)}
        </option>
      `
    )
    .join("");
  sourceLanguageSelect.disabled = !state.currentProjectId || Boolean(state.detail?.job?.running) || clipTaskRunning();
}

function renderStatusRail() {
  const presentation = deriveJobPresentation(state.detail);
  jobStage.textContent = presentation.stageLine;
  jobMessage.textContent = presentation.summaryLine;
  jobStep.textContent = presentation.detailLine;
  jobMessage.title = presentation.summaryTitle || presentation.summaryLine || "";
  jobStep.title = presentation.detailTitle || presentation.detailLine || "";
  jobStageProgress.textContent = presentation.stageProgressLine;
  jobElapsed.textContent = `已耗时 ${presentation.elapsedLine}`;
  jobEta.textContent = `预计剩余 ${presentation.etaLine}`;
  jobProgressFill.style.width = `${presentation.progressPercent}%`;
  jobStatusRail.className = `status-rail ${presentation.tone}`;
}

function renderSourceControls() {
  const hasProject = Boolean(state.currentProjectId);
  const hasVideo = Boolean(state.detail?.paths?.source_video);
  const running = Boolean(state.detail?.job?.running);
  const clipping = clipTaskRunning();
  const hasSourceSegments = Boolean(state.detail?.source_segments?.segments?.length);
  sourceVideo.controls = false;
  sourceVolumeSlider.value = String(state.pageState.sourceVolume);
  sourceSpeedSelect.value = state.pageState.sourceSpeed;
  syncPlaybackSettings();
  for (const button of document.querySelectorAll(".controls:not(.target-controls) button[data-action]")) {
    button.disabled = !hasVideo;
  }
  startSourceProcessButton.disabled = !hasProject || running || clipping;
  startSourceProcessButton.textContent = running ? "正在转写分段..." : "转写分段";
  startSourceProcessButton.dataset.hasSourceSegments = hasSourceSegments ? "true" : "false";
  if (startFullProcessButton) {
    startFullProcessButton.disabled = !hasProject || running || clipping;
    startFullProcessButton.textContent = running ? "转写合成执行中..." : "转写合成";
  }
  if (subtitleSyncToggle) {
    subtitleSyncToggle.checked = state.pageState.subtitleSync !== false;
  }
}

function mergedTargetSegments(detail) {
  if (!detail) {
    return [];
  }
  const sourceOrder = detail.source_segments?.segments || [];
  const draftById = new Map((detail.target_segments_draft?.segments || []).map((segment) => [segment.id, segment]));
  const alignedById = new Map((detail.target_segments_aligned?.segments || []).map((segment) => [segment.id, segment]));
  if (!sourceOrder.length) {
    return detail.target_segments_aligned?.segments?.length
      ? detail.target_segments_aligned.segments
      : detail.target_segments_draft?.segments || [];
  }
  const merged = [];
  for (const sourceSegment of sourceOrder) {
    const candidate = alignedById.get(sourceSegment.id) || draftById.get(sourceSegment.id);
    if (candidate) {
      merged.push(candidate);
    }
  }
  return merged;
}

function displayTargetSegments(detail) {
  if (!detail) {
    return [];
  }
  const sourceOrder = detail.source_segments?.segments || [];
  const merged = mergedTargetSegments(detail);
  const mergedById = new Map(merged.map((segment) => [segment.id, segment]));
  if (!sourceOrder.length) {
    return merged;
  }
  return sourceOrder.map((sourceSegment) => {
    const candidate = mergedById.get(sourceSegment.id);
    if (candidate) {
      return { ...candidate, placeholder: false };
    }
    return {
      id: sourceSegment.id,
      index: sourceSegment.index,
      start: sourceSegment.start,
      end: sourceSegment.end,
      source_start: sourceSegment.start,
      source_end: sourceSegment.end,
      text: "",
      source_text: sourceSegment.text,
      speaker: sourceSegment.speaker || "speaker_1",
      status: "pending",
      audio_path: null,
      placeholder: true,
    };
  });
}

function targetResynthesisRequired(detail = state.detail) {
  return mergedTargetSegments(detail).some((segment) => segment.status === "edited");
}

function hasUnsavedTargetDrafts(detail = state.detail) {
  const projectId = detail?.manifest?.id || state.currentProjectId;
  if (!projectId) {
    return false;
  }
  return mergedTargetSegments(detail).some((segment) => hasLocalDirtyDraft(projectId, "target", segment.id, segment.text));
}

function findSegment(side, segmentId) {
  if (side === "target") {
    return mergedTargetSegments(state.detail).find((item) => item.id === segmentId) || null;
  }
  return state.detail?.source_segments?.segments?.find((item) => item.id === segmentId) || null;
}

function matchingSegment(sourceSide, segmentId) {
  const otherSide = sourceSide === "source" ? "target" : "source";
  return findSegment(otherSide, segmentId);
}

function activeSegment(side, currentTime) {
  const segments = side === "target" ? mergedTargetSegments(state.detail) : (state.detail?.source_segments?.segments || []);
  return segments.find((segment) => currentTime >= segment.start && currentTime <= segment.end) || null;
}

function getSegmentContainer(side) {
  return side === "target" ? targetSegments : sourceSegments;
}

function subtitleSyncEnabled() {
  return Boolean(subtitleSyncToggle?.checked);
}

function wholeVideoPlaybackActive(side) {
  const video = side === "target" ? targetVideo : sourceVideo;
  return Boolean(video && !video.paused && !state.segmentPlayback[side] && state.playbackLeader === side);
}

function anyWholeVideoPlaybackActive() {
  return wholeVideoPlaybackActive("source") || wholeVideoPlaybackActive("target");
}

function playbackLeaderSide() {
  if (wholeVideoPlaybackActive("source")) {
    return "source";
  }
  if (wholeVideoPlaybackActive("target")) {
    return "target";
  }
  return null;
}

function withScrollLock(callback, holdMs = 32) {
  if (state.scrollLockHandle) {
    window.clearTimeout(state.scrollLockHandle);
  }
  state.isSyncingScroll = true;
  callback();
  state.scrollLockHandle = window.setTimeout(() => {
    state.isSyncingScroll = false;
    state.scrollLockHandle = null;
  }, holdMs);
}

function syncCaption(side, segment) {
  getSegmentContainer(side)?.querySelectorAll(".segment-row").forEach((node) => node.classList.remove("active"));
  if (segment) {
    document.getElementById(`${side}-${segment.id}`)?.classList.add("active");
  }
}

function segmentViewportState(side, segmentId) {
  const container = getSegmentContainer(side);
  const row = document.getElementById(`${side}-${segmentId}`);
  if (!container || !row) {
    return null;
  }
  const containerRect = container.getBoundingClientRect();
  const rowRect = row.getBoundingClientRect();
  const safePadding = Math.max(20, Math.min(56, container.clientHeight * 0.14));
  const safeTop = containerRect.top + safePadding;
  const safeBottom = containerRect.bottom - safePadding;
  const safeHeight = Math.max(safeBottom - safeTop, row.offsetHeight);
  const fullyVisible = rowRect.top >= containerRect.top && rowRect.bottom <= containerRect.bottom;
  let nextTop = container.scrollTop;
  if (row.offsetHeight >= safeHeight) {
    nextTop = row.offsetTop - safePadding;
  } else if (rowRect.top < safeTop) {
    nextTop = container.scrollTop - (safeTop - rowRect.top);
  } else if (rowRect.bottom > safeBottom) {
    nextTop = container.scrollTop + (rowRect.bottom - safeBottom);
  }
  const targetTop = Math.max(0, Math.min(nextTop, container.scrollHeight - container.clientHeight));
  const visible = (
    (rowRect.top >= safeTop && rowRect.bottom <= safeBottom)
    || (targetTop === container.scrollTop && fullyVisible)
  );
  return {
    container,
    visible,
    targetTop,
  };
}

function ensureSegmentVisible(side, segmentId, behavior = "auto") {
  const viewport = segmentViewportState(side, segmentId);
  if (!viewport || viewport.visible) {
    return;
  }
  withScrollLock(() => {
    viewport.container.scrollTo({ top: viewport.targetTop, behavior });
  }, behavior === "smooth" ? 260 : 180);
}

function mirrorSegmentPosition(fromSide, segmentId, offset = 0, behavior = "auto") {
  const toSide = fromSide === "source" ? "target" : "source";
  const container = getSegmentContainer(toSide);
  const row = document.getElementById(`${toSide}-${segmentId}`);
  if (!container || !row) {
    return;
  }
  const nextTop = Math.max(0, Math.min(row.offsetTop + offset, container.scrollHeight - container.clientHeight));
  container.scrollTo({ top: nextTop, behavior });
}

function visibleAnchor(side) {
  const container = getSegmentContainer(side);
  if (!container) {
    return null;
  }
  const rows = Array.from(container.querySelectorAll(".segment-row"));
  const top = container.scrollTop;
  const anchor = rows.find((row) => row.offsetTop + row.offsetHeight > top + 1) || rows.at(-1);
  if (!anchor) {
    return null;
  }
  return {
    segmentId: anchor.dataset.segmentId,
    offset: top - anchor.offsetTop,
  };
}

function syncScrollFrom(side) {
  if (!subtitleSyncEnabled() || state.isSyncingScroll || anyWholeVideoPlaybackActive()) {
    return;
  }
  const container = getSegmentContainer(side);
  const otherContainer = getSegmentContainer(side === "source" ? "target" : "source");
  if (!container || !otherContainer) {
    return;
  }
  const sourceMax = Math.max(0, container.scrollHeight - container.clientHeight);
  const targetMax = Math.max(0, otherContainer.scrollHeight - otherContainer.clientHeight);
  const nextTop = sourceMax > 0 && Math.abs(sourceMax - targetMax) > 1
    ? (container.scrollTop / sourceMax) * targetMax
    : container.scrollTop;
  const clampedTop = Math.max(0, Math.min(nextTop, targetMax));
  if (Math.abs(otherContainer.scrollTop - clampedTop) < 0.5) {
    return;
  }
  const otherSide = side === "source" ? "target" : "source";
  state.suppressedScrollEvents[otherSide].push({
    top: clampedTop,
    expiresAt: performance.now() + 160,
  });
  state.isSyncingScroll = true;
  otherContainer.scrollTop = clampedTop;
  state.isSyncingScroll = false;
  if (side === "source") {
    rememberSegmentScrollState(container.scrollTop, clampedTop);
  } else {
    rememberSegmentScrollState(clampedTop, container.scrollTop);
  }
}

function shouldIgnoreSuppressedScroll(side, top) {
  const queue = state.suppressedScrollEvents[side];
  if (!queue?.length) {
    return false;
  }
  const now = performance.now();
  while (queue.length && queue[0].expiresAt < now) {
    queue.shift();
  }
  const matchIndex = queue.findIndex((entry) => Math.abs(entry.top - top) < 1.5);
  if (matchIndex === -1) {
    return false;
  }
  queue.splice(matchIndex, 1);
  return true;
}

function scheduleScrollSync(side) {
  if (!canLeadScrollSync(side)) {
    return;
  }
  state.pendingScrollLeader = side;
  if (state.scrollSyncFrame) {
    return;
  }
  state.scrollSyncFrame = window.requestAnimationFrame(() => {
    state.scrollSyncFrame = null;
    const leader = state.pendingScrollLeader;
    state.pendingScrollLeader = null;
    if (leader) {
      syncScrollFrom(leader);
    }
    if (state.pendingScrollLeader) {
      scheduleScrollSync(state.pendingScrollLeader);
    }
  });
}

function scheduleScrollStatePersist() {
  if (state.scrollPersistTimer) {
    window.clearTimeout(state.scrollPersistTimer);
  }
  state.scrollPersistTimer = window.setTimeout(() => {
    state.scrollPersistTimer = null;
    writeLocalPageState({
      sourceSegmentsScrollTop: sourceSegments?.scrollTop || 0,
      targetSegmentsScrollTop: targetSegments?.scrollTop || 0,
    });
  }, 120);
}

function rememberSegmentScrollState(sourceTop = sourceSegments?.scrollTop || 0, targetTop = targetSegments?.scrollTop || 0) {
  state.pageState = {
    ...state.pageState,
    sourceSegmentsScrollTop: Number(sourceTop) || 0,
    targetSegmentsScrollTop: Number(targetTop) || 0,
  };
}

function captureLiveSegmentScrollState() {
  if (!sourceSegments || !targetSegments) {
    return;
  }
  rememberSegmentScrollState(sourceSegments.scrollTop, targetSegments.scrollTop);
}

function noteScrollGesture(side) {
  state.scrollGestureLeader = side;
  if (state.scrollGestureTimer) {
    window.clearTimeout(state.scrollGestureTimer);
  }
  state.scrollGestureTimer = window.setTimeout(() => {
    state.scrollGestureLeader = null;
    state.scrollGestureTimer = null;
  }, 220);
}

function canLeadScrollSync(side) {
  return !state.scrollGestureLeader || state.scrollGestureLeader === side;
}

function activeTextareaState() {
  const node = document.activeElement;
  if (!(node instanceof HTMLTextAreaElement)) {
    return null;
  }
  const side = node.dataset.side;
  const segmentId = node.dataset.segmentId || node.dataset.targetSegmentId;
  if (side && segmentId) {
    return {
      projectId: node.dataset.projectId || state.currentProjectId,
      side,
      segmentId,
      selectionStart: node.selectionStart ?? 0,
      selectionEnd: node.selectionEnd ?? 0,
      scrollTop: node.scrollTop ?? 0,
    };
  }
  return null;
}

function persistActiveEditorState(textarea) {
  if (!(textarea instanceof HTMLTextAreaElement)) {
    return;
  }
  const segmentId = textarea.dataset.segmentId || textarea.dataset.targetSegmentId;
  const side = textarea.dataset.side;
  if (!segmentId || !side) {
    return;
  }
  writeLocalPageState({
    activeEditor: {
      projectId: textarea.dataset.projectId || state.currentProjectId,
      side,
      segmentId,
      selectionStart: textarea.selectionStart ?? 0,
      selectionEnd: textarea.selectionEnd ?? 0,
      scrollTop: textarea.scrollTop ?? 0,
    },
  });
}

function clearActiveEditorState() {
  if (state.pageState.activeEditor) {
    writeLocalPageState({ activeEditor: null });
  }
}

function persistViewportStateBeforeUnload() {
  state.isPageUnloading = true;
  writeLocalPageState({
    sourceSegmentsScrollTop: sourceSegments?.scrollTop || 0,
    targetSegmentsScrollTop: targetSegments?.scrollTop || 0,
  });
  const activeEditor = activeTextareaState();
  if (activeEditor) {
    writeLocalPageState({ activeEditor });
    return;
  }
  clearActiveEditorState();
}

function restoreActiveEditorState() {
  const editor = state.pageState.activeEditor;
  if (!editor || editor.projectId !== state.currentProjectId) {
    return;
  }
  const container = editor.side === "target" ? targetSegments : sourceSegments;
  const textarea = container?.querySelector(
    `.segment-text[data-project-id="${CSS.escape(editor.projectId)}"][data-side="${CSS.escape(editor.side)}"][data-segment-id="${CSS.escape(editor.segmentId)}"]`
  );
  if (!(textarea instanceof HTMLTextAreaElement)) {
    return;
  }
  textarea.focus({ preventScroll: true });
  const textLength = textarea.value.length;
  const selectionStart = Math.max(0, Math.min(Number(editor.selectionStart) || 0, textLength));
  const selectionEnd = Math.max(selectionStart, Math.min(Number(editor.selectionEnd) || selectionStart, textLength));
  textarea.setSelectionRange(selectionStart, selectionEnd);
  textarea.scrollTop = Math.max(0, Number(editor.scrollTop) || 0);
}

function updateCaptionState(side) {
  const video = side === "target" ? targetVideo : sourceVideo;
  const leaderSide = subtitleSyncEnabled() ? playbackLeaderSide() : null;
  const mirroredSegment = leaderSide && leaderSide !== side
    ? matchingSegment(leaderSide, activeSegment(leaderSide, (leaderSide === "source" ? sourceVideo : targetVideo).currentTime || 0)?.id)
    : null;
  const segment = mirroredSegment || activeSegment(side, video.currentTime || 0);
  syncCaption(side, segment);
  const previousId = state.activeSegmentIds[side];
  state.activeSegmentIds[side] = segment?.id || null;
  const wholeVideoPlayback = wholeVideoPlaybackActive(side);
  if (
    wholeVideoPlayback &&
    segment?.id &&
    !activeTextareaState() &&
    (segment.id !== previousId || !segmentViewportState(side, segment.id)?.visible)
  ) {
    ensureSegmentVisible(side, segment.id);
  }
  if (segment && subtitleSyncEnabled()) {
    if (mirroredSegment) {
      if (!activeTextareaState() && !segmentViewportState(side, segment.id)?.visible) {
        ensureSegmentVisible(side, segment.id);
      }
      return;
    }
    const otherSide = side === "source" ? "target" : "source";
    const previousPeerId = state.activeSegmentIds[otherSide];
    const peerSegment = matchingSegment(side, segment.id);
    syncCaption(otherSide, peerSegment);
    state.activeSegmentIds[otherSide] = peerSegment?.id || null;
    if (
      wholeVideoPlayback &&
      peerSegment?.id &&
      !activeTextareaState() &&
      (peerSegment.id !== previousPeerId || !segmentViewportState(otherSide, peerSegment.id)?.visible)
    ) {
      ensureSegmentVisible(otherSide, peerSegment.id);
    }
  } else if (!segment) {
    state.activeSegmentIds[side] = null;
  }
}

function clearSegmentPlayback(side = null) {
  if (side) {
    state.segmentPlayback[side] = null;
    return;
  }
  state.segmentPlayback.source = null;
  state.segmentPlayback.target = null;
}

function attachVideoEvents(video, side) {
  video.addEventListener("play", () => {
    if (!state.segmentPlayback[side]) {
      state.playbackLeader = side;
      updateCaptionState(side);
    }
  });
  video.addEventListener("pause", () => {
    pauseCurrentClipAudio(side);
    if (!state.segmentPlayback[side] && state.playbackLeader === side) {
      state.playbackLeader = null;
    }
  });
  video.addEventListener("ended", () => {
    if (state.currentClipAudioSide === side) {
      stopCurrentClipAudio();
    }
    if (state.playbackLeader === side) {
      state.playbackLeader = null;
    }
    state.segmentPlayback[side] = null;
  });
  video.addEventListener("timeupdate", () => {
    updateCaptionState(side);
    const active = state.segmentPlayback[side];
    if (active && video.currentTime >= active.end) {
      video.pause();
      if (state.currentClipAudioSide === side) {
        stopCurrentClipAudio();
      }
      state.segmentPlayback[side] = null;
      if (state.playbackLeader === side) {
        state.playbackLeader = null;
      }
    }
  });
}

attachVideoEvents(sourceVideo, "source");
attachVideoEvents(targetVideo, "target");

function targetEmptyMessage(detail) {
  if (!detail) {
    return "请选择左侧项目，或先导入一个视频。";
  }
  const sourceCount = detail.source_segments?.segments?.length || 0;
  const pendingCount = pendingReviewSuggestions(detail.source_correction_review).length;
  if (!sourceCount) {
    return "还没有 source 文本。点击“转写分段”开始处理。";
  }
  if (pendingCount > 0) {
    return `source 文本还有 ${pendingCount} 条纠错待确认，确认后再开始翻译合成。`;
  }
  if (detail.job?.running && detail.job.stage === "translating_target") {
    return "正在逐段翻译合成，完成的段落会陆续显示在这里。";
  }
  if (detail.job?.running && detail.job.stage === "aligning_target") {
    return "正在进行时序对齐，已完成的段落会逐步切换为最终结果。";
  }
  return "还没有 target 文本。点击“翻译合成”开始处理。";
}

function renderTargetPanel() {
  if (!targetSegments) {
    return;
  }
  const detail = state.detail;
  const targetRows = displayTargetSegments(detail);
  const projectId = detail?.manifest?.id || state.currentProjectId;
  const hasSavedEdits = targetResynthesisRequired(detail);
  const hasDirtyDrafts = hasUnsavedTargetDrafts(detail);
  const targetLanguage = detail?.manifest?.target_language || state.pageState.targetLanguage || "English";
  const languageOptions = state.systemStatus?.options?.target_language || TARGET_LANGUAGE_OPTIONS;
  targetLanguageSelect.innerHTML = languageOptions
    .map(
      (option) => `
        <option value="${escapeHtml(option.value)}"${option.value === targetLanguage ? " selected" : ""}>
          ${escapeHtml(option.label)}
        </option>
      `
    )
    .join("");
  const hasProject = Boolean(state.currentProjectId);
  const running = Boolean(detail?.job?.running);
  const clipping = clipTaskRunning();
  const sourceCount = detail?.source_segments?.segments?.length || 0;
  const pendingCount = pendingReviewSuggestions(detail?.source_correction_review).length;
  targetLanguageSelect.disabled = !hasProject || running || clipping;
  targetVolumeSlider.value = String(state.pageState.targetVolume);
  targetVolumeSlider.disabled = false;
  if (targetSpeedSelect) {
    targetSpeedSelect.value = state.pageState.targetSpeed;
    targetSpeedSelect.disabled = false;
  }
  const targetStage = detail?.job?.stage || "";
  const targetStageRunning = running && ["translating_target", "synthesizing_target", "aligning_target"].includes(targetStage);
  startTargetProcessButton.textContent = targetStageRunning
    ? (targetStage === "synthesizing_target" ? "正在重新合成..." : "正在翻译合成...")
    : ((hasSavedEdits || hasDirtyDrafts) ? "重新合成" : "翻译合成");
  startTargetProcessButton.disabled = !hasProject || running || clipping || !sourceCount || pendingCount > 0;
  targetVideo.controls = false;
  const activeEditor = activeTextareaState();
  if (
    activeEditor &&
    activeEditor.projectId === state.currentProjectId &&
    activeEditor.side === "target" &&
    targetSegments.querySelector(
      `.segment-text[data-project-id="${CSS.escape(activeEditor.projectId)}"][data-side="target"][data-segment-id="${CSS.escape(activeEditor.segmentId)}"]`
    )
  ) {
    return;
  }
  if (!detail) {
    targetSegments.innerHTML = `<div class="empty-block">${escapeHtml(targetEmptyMessage(detail))}</div>`;
    return;
  }
  const sourceRows = detail.source_segments?.segments || [];
  if (!sourceRows.length) {
    targetSegments.innerHTML = `<div class="empty-block">${escapeHtml(targetEmptyMessage(detail))}</div>`;
    return;
  }
  targetSegments.innerHTML = "";
  for (const segment of targetRows) {
    const isPlaceholder = Boolean(segment.placeholder);
    const textValue = isPlaceholder ? "" : (getSegmentDraft(projectId, "target", segment.id) ?? segment.text);
    const dirty = segmentTextChanged(textValue, segment.text);
    const segmentRow = document.createElement("article");
    segmentRow.className = `segment-row target-segment-row${isPlaceholder ? " is-placeholder" : ""}`;
    segmentRow.id = `target-${segment.id}`;
    segmentRow.dataset.segmentId = segment.id;
    segmentRow.innerHTML = `
      <div class="segment-actions">
        <div class="segment-index">#${String((segment.index ?? 0) + 1).padStart(2, "0")}</div>
        <div class="segment-time">${formatClock(segment.start)} - ${formatClock(segment.end)}</div>
        <div class="segment-status">${escapeHtml(isPlaceholder ? "待生成" : (segment.status || "ready"))}</div>
        <button type="button" data-target-segment-action="play" data-segment-id="${escapeHtml(segment.id)}"${segment.audio_path && !isPlaceholder ? "" : " disabled"}>播放</button>
        <button type="button" class="save" data-target-segment-action="save" data-segment-id="${escapeHtml(segment.id)}"${dirty && !isPlaceholder ? "" : " disabled"}>保存</button>
      </div>
      <textarea class="segment-text" data-project-id="${escapeHtml(projectId)}" data-side="target" data-segment-id="${escapeHtml(segment.id)}"${isPlaceholder ? " disabled" : ""}>${escapeHtml(textValue)}</textarea>
    `;
    if (isPlaceholder) {
      targetSegments.appendChild(segmentRow);
      continue;
    }
    const textarea = segmentRow.querySelector(".segment-text");
    textarea.addEventListener("focus", () => {
      persistActiveEditorState(textarea);
    });
    textarea.addEventListener("input", () => {
      setSegmentDraft(projectId, "target", segment.id, textarea.value);
      persistActiveEditorState(textarea);
      setSegmentSaveButtonState(segmentRow, segmentTextChanged(textarea.value, segment.text));
      startTargetProcessButton.textContent = "重新合成";
    });
    textarea.addEventListener("click", () => {
      persistActiveEditorState(textarea);
    });
    textarea.addEventListener("keyup", () => {
      persistActiveEditorState(textarea);
      setSegmentSaveButtonState(segmentRow, segmentTextChanged(textarea.value, segment.text));
    });
    textarea.addEventListener("select", () => {
      persistActiveEditorState(textarea);
    });
    textarea.addEventListener("scroll", () => {
      persistActiveEditorState(textarea);
    });
    textarea.addEventListener("blur", () => {
      window.setTimeout(() => {
        if (state.isPageUnloading) {
          return;
        }
        const nextActive = activeTextareaState();
        if (!nextActive || nextActive.projectId !== projectId || nextActive.segmentId !== segment.id || nextActive.side !== "target") {
          clearActiveEditorState();
        }
      }, 0);
    });
    textarea.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      persistActiveEditorState(textarea);
      state.textContextTarget = textarea;
      showTextContextMenu(event.clientX, event.clientY);
    });
    targetSegments.appendChild(segmentRow);
  }
}

function renderSourceReviewToolbar() {
  return;
}

function reviewChangeListHtml(changes) {
  if (!changes?.length) {
    return "<li>Codex 认为这段存在可能的 ASR 错误，建议人工确认。</li>";
  }
  return changes
    .map((item) => `<li><code>${escapeHtml(item.from_text || "")}</code> → <code>${escapeHtml(item.to_text || "")}</code></li>`)
    .join("");
}

function sourceReviewPanelHtml(suggestion) {
  if (!suggestion) {
    return "";
  }
  const shouldShowPanel = ["pending", "accepted", "rejected", "customized", "failed"].includes(suggestion.status);
  if (!shouldShowPanel) {
    return "";
  }
  const statusLabelMap = {
    queued: "排队中",
    processing: "处理中",
    pending: "待确认",
    accepted: "已接受",
    rejected: "已拒绝",
    customized: "已自定义",
    unchanged: "无需修改",
    failed: "失败",
  };
  const note = suggestion.error
    ? `<div class="review-note error">${escapeHtml(suggestion.error)}</div>`
    : suggestion.status === "pending"
      ? `<div class="review-note">以下是 Codex 识别出的可能 ASR 错误，请确认是否采用。</div>`
      : suggestion.status === "accepted"
        ? `<div class="review-note">这条已采用 Codex 建议。</div>`
        : suggestion.status === "rejected"
          ? `<div class="review-note">这条已保留原始识别文本。</div>`
          : suggestion.status === "customized"
            ? `<div class="review-note">这条已改成你确认后的自定义文本。</div>`
            : "";
  const suggestionBlock = suggestion.suggested_text && suggestion.suggested_text !== suggestion.original_text
    ? `
      <div class="review-text-pair">
        <span>Codex 建议</span>
        <strong>${escapeHtml(suggestion.suggested_text)}</strong>
      </div>
    `
    : "";
  const actions = suggestion.status === "pending"
    ? `
      <div class="review-actions">
        <label class="review-batch-toggle">
          <input type="checkbox" data-source-review-apply-all ${state.bulkPendingEnabled ? "checked" : ""}>
          <span>全部待确认项</span>
        </label>
        <button data-review-action="accept" data-segment-id="${escapeHtml(suggestion.segment_id)}">接受</button>
        <button data-review-action="reject" data-segment-id="${escapeHtml(suggestion.segment_id)}">拒绝</button>
      </div>
    `
    : "";
  return `
    <div class="source-review-panel ${escapeHtml(suggestion.status)}">
      <div class="review-head">
        <strong>纠错建议</strong>
        <span class="review-status ${escapeHtml(suggestion.status)}">${escapeHtml(statusLabelMap[suggestion.status] || suggestion.status)}</span>
      </div>
      ${note}
      <div class="review-text-pair">
        <span>原始识别</span>
        <strong>${escapeHtml(suggestion.original_text)}</strong>
      </div>
      ${suggestionBlock}
      <div class="review-change-list">
        <span>修改提示</span>
        <ul>${reviewChangeListHtml(suggestion.changes)}</ul>
      </div>
      ${actions}
    </div>
  `;
}

function renderSegments() {
  const detail = state.detail;
  const activeEditor = activeTextareaState();
  if (
    activeEditor &&
    activeEditor.projectId === state.currentProjectId &&
    activeEditor.side === "source" &&
    sourceSegments.querySelector(
      `.segment-text[data-project-id="${CSS.escape(activeEditor.projectId)}"][data-segment-id="${CSS.escape(activeEditor.segmentId)}"]`
    )
  ) {
    return;
  }
  sourceSegments.innerHTML = "";
  if (!detail) {
    sourceSegments.innerHTML = '<div class="empty-block">请选择左侧项目，或先导入一个视频。</div>';
    return;
  }
  const segments = detail.source_segments?.segments || [];
  if (!segments.length) {
    sourceSegments.innerHTML = '<div class="empty-block">还没有 source 文本。点击“转写分段”开始处理。</div>';
    return;
  }

  const reviewVisible = isReviewVisible(state.currentProjectId);
  const suggestionById = reviewSuggestionMap(detail.source_correction_review);
  const projectId = detail.manifest?.id || state.currentProjectId;

  for (const segment of segments) {
    const suggestion = suggestionById.get(segment.id);
    const textValue = getSegmentDraft(projectId, "source", segment.id) ?? segment.text;
    const dirty = segmentTextChanged(textValue, segment.text);
    const segmentRow = document.createElement("article");
    segmentRow.className = "segment-row";
    segmentRow.id = `source-${segment.id}`;
    segmentRow.dataset.segmentId = segment.id;
    const suggestionHtml = reviewVisible ? sourceReviewPanelHtml(suggestion) : "";
    segmentRow.innerHTML = `
      <div class="segment-actions">
        <div class="segment-index">#${String((segment.index ?? 0) + 1).padStart(2, "0")}</div>
        <div class="segment-time">${formatClock(segment.start)} - ${formatClock(segment.end)}</div>
        <div class="segment-status">${escapeHtml(segment.status || "ready")}</div>
        <button type="button" data-segment-action="play" data-segment-id="${escapeHtml(segment.id)}"${segment.audio_path ? "" : " disabled"}>播放</button>
        <button type="button" class="save" data-segment-action="save" data-segment-id="${escapeHtml(segment.id)}"${dirty ? "" : " disabled"}>保存</button>
      </div>
      <textarea class="segment-text" data-project-id="${escapeHtml(projectId)}" data-side="source" data-segment-id="${escapeHtml(segment.id)}">${escapeHtml(textValue)}</textarea>
      ${suggestionHtml}
    `;
    const textarea = segmentRow.querySelector(".segment-text");
    textarea.addEventListener("focus", () => {
      persistActiveEditorState(textarea);
    });
    textarea.addEventListener("input", () => {
      setSegmentDraft(projectId, "source", segment.id, textarea.value);
      persistActiveEditorState(textarea);
      setSegmentSaveButtonState(segmentRow, segmentTextChanged(textarea.value, segment.text));
    });
    textarea.addEventListener("click", () => {
      persistActiveEditorState(textarea);
    });
    textarea.addEventListener("keyup", () => {
      persistActiveEditorState(textarea);
      setSegmentSaveButtonState(segmentRow, segmentTextChanged(textarea.value, segment.text));
    });
    textarea.addEventListener("select", () => {
      persistActiveEditorState(textarea);
    });
    textarea.addEventListener("scroll", () => {
      persistActiveEditorState(textarea);
    });
    textarea.addEventListener("blur", () => {
      window.setTimeout(() => {
        if (state.isPageUnloading) {
          return;
        }
        const nextActive = activeTextareaState();
        if (!nextActive || nextActive.projectId !== projectId || nextActive.segmentId !== segment.id) {
          clearActiveEditorState();
        }
      }, 0);
    });
    textarea.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      persistActiveEditorState(textarea);
      state.textContextTarget = textarea;
      showTextContextMenu(event.clientX, event.clientY);
    });
    sourceSegments.appendChild(segmentRow);
  }
}

function renderWorkspace() {
  renderSourceLanguageSelect();
  renderSourceControls();
  renderSourceReviewToolbar();
  renderSegments();
  renderTargetPanel();
  setSourceVideo(mediaUrl(state.detail?.paths?.source_video, sourceMediaVersion(state.detail)));
  const targetVisualPath = state.detail?.paths?.target_video || state.detail?.paths?.source_video;
  const targetVisualVersion = state.detail?.paths?.target_video ? targetMediaVersion(state.detail) : sourceMediaVersion(state.detail);
  setTargetVideo(mediaUrl(targetVisualPath, targetVisualVersion));
  restoreSegmentScrollState();
  window.requestAnimationFrame(() => {
    syncSegmentRowHeights();
    restoreSegmentScrollState();
    restoreActiveEditorState();
    updateCaptionState("source");
    updateCaptionState("target");
  });
}

function renderAll() {
  renderModelSettings();
  renderProjectList();
  renderStatusRail();
  renderWorkspace();
  renderClipLockState();
  renderClipDialogState();
}

async function loadSystemStatus() {
  state.systemStatus = await request("/api/system/status");
}

async function loadSidebarState() {
  state.sidebarState = await request("/api/ui/sidebar-state");
}

async function loadClipTask() {
  const payload = await request("/api/clip-task");
  state.clipTask = payload.task || null;
}

async function loadProjects() {
  const payload = await request("/api/projects");
  state.projects = payload.projects || [];
}

function ensureSelectedProject() {
  const existingIds = new Set(state.projects.map((item) => item.id));
  const preferredId = state.currentProjectId || state.sidebarState.selected_project_id;
  if (preferredId && existingIds.has(preferredId)) {
    state.currentProjectId = preferredId;
    return;
  }
  state.currentProjectId = state.projects[0]?.id || null;
}

async function loadCurrentProjectDetail() {
  if (!state.currentProjectId) {
    state.detail = null;
    return;
  }
  state.detail = await request(`/api/projects/${encodeURIComponent(state.currentProjectId)}`);
}

async function refreshAppState({ includeSystemStatus = false, preserveSelection = true } = {}) {
  captureLiveSegmentScrollState();
  if (includeSystemStatus || !state.systemStatus) {
    await loadSystemStatus();
  }
  if (!preserveSelection || !state.sidebarState) {
    await loadSidebarState();
  }
  await loadClipTask();
  await loadProjects();
  ensureSelectedProject();
  if (state.currentProjectId) {
    await loadCurrentProjectDetail();
  } else {
    state.detail = null;
  }
  renderAll();
  ensureClipDialogVisibility();
  if (clipTaskRunning()) {
    startClipPolling();
  } else if (!clipTaskTerminal()) {
    stopClipPolling();
  }
}

async function selectProject(projectId) {
  if (clipTaskRunning()) {
    return;
  }
  if (projectId === state.currentProjectId) {
    return;
  }
  stopCurrentClipAudio();
  clearSegmentPlayback();
  state.playbackLeader = null;
  sourceVideo.pause();
  targetVideo?.pause();
  state.currentProjectId = projectId;
  state.bulkPendingEnabled = false;
  await persistSidebarState({ selected_project_id: projectId });
  await loadCurrentProjectDetail();
  renderAll();
}

function stopClipPolling() {
  window.clearInterval(state.clipPollTimer);
  state.clipPollTimer = null;
}

async function pollClipTask() {
  try {
    const wasRunning = clipTaskRunning();
    const previousTaskId = state.clipTask?.id || "";
    await loadClipTask();
    const isRunning = clipTaskRunning();
    renderClipDialogState();
    renderClipLockState();
    if (isRunning) {
      ensureClipDialogVisibility();
    }
    if (wasRunning !== isRunning) {
      renderAll();
    }
    if (!state.clipTask) {
      stopClipPolling();
      return;
    }
    if (clipTaskTerminal()) {
      stopClipPolling();
      await refreshAppState();
      if (!clipDialog.open && state.clipTask?.id === previousTaskId) {
        renderClipDialogState();
      }
    }
  } catch (_error) {
    // Keep the current dialog state if clip polling fails transiently.
  }
}

function startClipPolling() {
  if (!clipTaskRunning()) {
    stopClipPolling();
    return;
  }
  if (state.clipPollTimer) {
    return;
  }
  state.clipPollTimer = window.setInterval(() => {
    void pollClipTask();
  }, CLIP_TASK_POLL_INTERVAL_MS);
}

async function updateRuntimeSetting(key, value) {
  if (key === "tts_model") {
    writeLocalPageState({ ttsModel: value });
  }
  state.systemStatus = await request("/api/system/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ [key]: value }),
  });
  state.renderedModelSettingsKey = null;
}

async function updateProjectLanguage(value) {
  if (!state.currentProjectId) {
    return;
  }
  await request(`/api/projects/${encodeURIComponent(state.currentProjectId)}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_language: value }),
  });
  await refreshAppState();
}

async function updateTargetLanguage(value) {
  if (!state.currentProjectId) {
    return;
  }
  await request(`/api/projects/${encodeURIComponent(state.currentProjectId)}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_language: value }),
  });
  writeLocalPageState({ targetLanguage: value });
  await refreshAppState();
}

async function importVideo(file) {
  const payload = await request(
    `/api/projects/import?filename=${encodeURIComponent(file.name)}&name=${encodeURIComponent(stemFromFilename(file.name))}`,
    {
      method: "POST",
      body: file,
    }
  );
  await loadProjects();
  state.currentProjectId = payload.project?.id || state.projects[0]?.id || null;
  await persistSidebarState({ selected_project_id: state.currentProjectId });
  await loadCurrentProjectDetail();
  renderAll();
}

async function startSourceProcess() {
  if (!state.currentProjectId) {
    return;
  }
  const hasExistingOutput = Boolean(state.detail?.source_segments?.segments?.length || state.detail?.source_correction_review?.suggestions?.length);
  if (hasExistingOutput && !window.confirm("重新执行会清空当前转写结果、切段音频和纠错记录，确定继续吗？")) {
    return;
  }
  await request(`/api/projects/${encodeURIComponent(state.currentProjectId)}/transcribe-source`, { method: "POST" });
  await refreshAppState();
}

async function startTargetProcess() {
  if (!state.currentProjectId) {
    return;
  }
  const sourceCount = state.detail?.source_segments?.segments?.length || 0;
  const pendingCount = pendingReviewSuggestions(state.detail?.source_correction_review).length;
  const hasUnsavedEdits = hasUnsavedTargetDrafts(state.detail);
  const needsResynthesis = targetResynthesisRequired(state.detail);
  if (!sourceCount) {
    window.alert("当前项目还没有 source 文本，请先执行转写分段。");
    return;
  }
  if (pendingCount > 0) {
    window.alert(`当前还有 ${pendingCount} 条 source 纠错待确认，请先处理完再执行翻译合成。`);
    return;
  }
  if (hasUnsavedEdits) {
    window.alert("请先保存已修改的 target 文本后再重新合成。");
    return;
  }
  if (needsResynthesis) {
    if (!window.confirm("将基于当前 target 文本重新合成语音并更新拼接结果，确定继续吗？")) {
      return;
    }
    await request(`/api/projects/${encodeURIComponent(state.currentProjectId)}/translate-target`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "resynthesize" }),
    });
    await refreshAppState();
    return;
  }
  const hasTargetOutput = Boolean(
    state.detail?.target_segments_draft?.segments?.length ||
    state.detail?.target_segments_aligned?.segments?.length ||
    state.detail?.paths?.target_track
  );
  if (hasTargetOutput && !window.confirm("重新执行会清空当前 target 文本、分段语音和对齐结果，确定继续吗？")) {
    return;
  }
  await request(`/api/projects/${encodeURIComponent(state.currentProjectId)}/translate-target`, { method: "POST" });
  await refreshAppState();
}

async function startFullProcess() {
  if (!state.currentProjectId) {
    return;
  }
  const hasAnyOutput = Boolean(
    state.detail?.source_segments?.segments?.length ||
    state.detail?.source_correction_review?.suggestions?.length ||
    state.detail?.target_segments_draft?.segments?.length ||
    state.detail?.target_segments_aligned?.segments?.length
  );
  if (hasAnyOutput && !window.confirm("重新执行会从头清空当前转写、纠错和翻译合成结果，确定继续吗？")) {
    return;
  }
  await request(`/api/projects/${encodeURIComponent(state.currentProjectId)}/run-full`, { method: "POST" });
  await refreshAppState();
}

function findCurrentSegment(segmentId) {
  return state.detail?.source_segments?.segments?.find((item) => item.id === segmentId) || null;
}

function findCurrentTargetSegment(segmentId) {
  return mergedTargetSegments(state.detail).find((item) => item.id === segmentId) || null;
}

function findCurrentSuggestion(segmentId) {
  return state.detail?.source_correction_review?.suggestions?.find((item) => item.segment_id === segmentId) || null;
}

async function playSegmentAudio(segmentId, side = "source") {
  const segment = findSegment(side, segmentId);
  if (!segment) {
    return;
  }
  const video = side === "target" ? targetVideo : sourceVideo;
  const targetUsesFallbackVisual = side === "target" && targetVideoUsesSourceVisual();
  const hasPlayableVideo = side === "source"
    ? Boolean(state.detail?.paths?.source_video)
    : Boolean(state.detail?.paths?.target_video || state.detail?.paths?.source_video);
  if (hasPlayableVideo && video) {
    stopCurrentClipAudio();
    clearSegmentPlayback();
    state.playbackLeader = null;
    sourceVideo.pause();
    targetVideo.pause();
    video.currentTime = Math.max(0, Number(segment.start || 0));
    state.segmentPlayback[side] = segment;
    syncCaption(side, segment);
    ensureSegmentVisible(side, segment.id, "smooth");
    if (subtitleSyncEnabled()) {
      const otherSide = side === "source" ? "target" : "source";
      const otherVideo = otherSide === "source" ? sourceVideo : targetVideo;
      const peerSegment = matchingSegment(side, segment.id);
      if (peerSegment && otherVideo) {
        otherVideo.pause();
        otherVideo.currentTime = Math.max(0, Number(peerSegment.start || 0));
        syncCaption(otherSide, peerSegment);
        ensureSegmentVisible(otherSide, peerSegment.id);
      }
    }
    if (side === "target" && targetUsesFallbackVisual && segment.audio_path) {
      const audio = new Audio(mediaUrl(segment.audio_path, targetMediaVersion(state.detail)));
      state.currentClipAudio = audio;
      state.currentClipAudioSide = side;
      audio.addEventListener("ended", () => {
        if (state.currentClipAudio === audio) {
          video.pause();
          state.segmentPlayback[side] = null;
          state.currentClipAudio = null;
          state.currentClipAudioSide = "source";
        }
      });
      syncPlaybackSettings();
      await Promise.all([
        video.play().catch(() => {}),
        audio.play().catch(() => {}),
      ]);
      return;
    }
    await video.play().catch(() => {});
    return;
  }
  if (!segment.audio_path) {
    return;
  }
  stopCurrentClipAudio();
  clearSegmentPlayback();
  sourceVideo.pause();
  targetVideo.pause();
  const audio = new Audio(mediaUrl(segment.audio_path));
  state.currentClipAudio = audio;
  state.currentClipAudioSide = side;
  syncCaption(side, segment);
  ensureSegmentVisible(side, segment.id, "smooth");
  if (subtitleSyncEnabled()) {
    const peerSegment = matchingSegment(side, segment.id);
    if (peerSegment) {
      syncCaption(side === "source" ? "target" : "source", peerSegment);
      ensureSegmentVisible(side === "source" ? "target" : "source", peerSegment.id);
    }
  }
  audio.addEventListener("ended", () => {
    if (state.currentClipAudio === audio) {
      state.currentClipAudio = null;
      state.currentClipAudioSide = "source";
    }
  });
  syncPlaybackSettings();
  await audio.play();
}

function jumpToSegment(segmentId) {
  const segment = findCurrentSegment(segmentId);
  if (!segment) {
    return;
  }
  stopCurrentClipAudio();
  sourceVideo.currentTime = Math.max(0, Number(segment.start || 0));
  sourceVideo.play().catch(() => {});
  const card = sourceSegments.querySelector(`.segment-text[data-segment-id="${CSS.escape(segmentId)}"]`)?.closest(".segment-row");
  card?.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function saveSegmentText(segmentId) {
  if (!state.currentProjectId) {
    return;
  }
  const textarea = sourceSegments.querySelector(`.segment-text[data-segment-id="${CSS.escape(segmentId)}"]`);
  if (!textarea) {
    return;
  }
  const text = textarea.value.trim();
  if (!text) {
    window.alert("正文不能为空。");
    return;
  }
  const suggestion = findCurrentSuggestion(segmentId);
  if (suggestion && suggestion.status === "pending") {
    await request(`/api/projects/${encodeURIComponent(state.currentProjectId)}/source-corrections/${encodeURIComponent(segmentId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "custom", text }),
    });
  } else {
    await request(`/api/projects/${encodeURIComponent(state.currentProjectId)}/segments/${encodeURIComponent(segmentId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
  }
  clearSegmentDraft(state.currentProjectId, "source", segmentId);
  const activeEditor = activeTextareaState();
  if (activeEditor?.side === "source" && activeEditor.segmentId === segmentId) {
    document.activeElement?.blur?.();
    clearActiveEditorState();
  }
  await refreshAppState();
}

async function saveTargetSegmentText(segmentId) {
  if (!state.currentProjectId) {
    return;
  }
  const textarea = targetSegments.querySelector(`.segment-text[data-side="target"][data-segment-id="${CSS.escape(segmentId)}"]`);
  if (!textarea) {
    return;
  }
  const text = textarea.value.trim();
  if (!text) {
    window.alert("正文不能为空。");
    return;
  }
  await request(`/api/projects/${encodeURIComponent(state.currentProjectId)}/target-segments/${encodeURIComponent(segmentId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  clearSegmentDraft(state.currentProjectId, "target", segmentId);
  const activeEditor = activeTextareaState();
  if (activeEditor?.side === "target" && activeEditor.segmentId === segmentId) {
    document.activeElement?.blur?.();
    clearActiveEditorState();
  }
  await refreshAppState();
}

async function applyReviewAction(segmentId, action) {
  if (!state.currentProjectId) {
    return;
  }
  if (state.bulkPendingEnabled) {
    await request(`/api/projects/${encodeURIComponent(state.currentProjectId)}/source-corrections/bulk`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
  } else {
    await request(`/api/projects/${encodeURIComponent(state.currentProjectId)}/source-corrections/${encodeURIComponent(segmentId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
  }
  await refreshAppState();
}

function showProjectContextMenu(projectId, x, y) {
  if (clipTaskRunning()) {
    return;
  }
  state.projectContextProjectId = projectId;
  renderProjectList();
  projectContextMenu.querySelectorAll("button[data-context-action]").forEach((button) => {
    button.disabled = clipTaskRunning();
  });
  projectContextMenu.hidden = false;
  projectContextMenu.style.left = `${x}px`;
  projectContextMenu.style.top = `${y}px`;
}

function hideProjectContextMenu() {
  const shouldRerender = Boolean(state.projectContextProjectId);
  projectContextMenu.hidden = true;
  state.projectContextProjectId = null;
  if (shouldRerender) {
    renderProjectList();
  }
}

function showTextContextMenu(x, y) {
  textContextMenu.hidden = false;
  textContextMenu.style.left = `${x}px`;
  textContextMenu.style.top = `${y}px`;
}

function hideTextContextMenu() {
  textContextMenu.hidden = true;
  state.textContextTarget = null;
}

function renderClipDialogState() {
  const task = state.clipTask;
  const running = clipTaskRunning();
  const terminal = clipTaskTerminal();
  clipStartInput.disabled = running;
  clipEndInput.disabled = running;
  clipCancelButton.disabled = running;
  clipSubmitButton.disabled = running;
  clipCancelButton.textContent = terminal ? "关闭" : "取消";
  clipSubmitButton.textContent = running ? "截取中..." : "确认";
  if (!task) {
    clipProgress.hidden = true;
    clipProgressFill.style.width = "0%";
    clipProgressStage.textContent = "等待开始";
    clipProgressPercent.textContent = "0%";
    clipProgressDetail.textContent = "尚未开始截取。";
    return;
  }
  if (Number.isFinite(Number(task.requested_start_seconds))) {
    clipStartInput.value = formatClock(task.requested_start_seconds);
  }
  if (Number.isFinite(Number(task.adjusted_end_seconds ?? task.requested_end_seconds))) {
    clipEndInput.value = formatClock(task.adjusted_end_seconds ?? task.requested_end_seconds);
  }
  const percent = Math.max(0, Math.min(Number(task.progress_percent) || 0, 100));
  const visualPercent = terminal ? percent : Math.min(percent, 99);
  const displayPercent = terminal ? Math.round(percent) : Math.min(99, Math.floor(percent));
  clipProgress.hidden = false;
  clipProgressFill.style.width = `${visualPercent}%`;
  clipProgressStage.textContent = clipTaskStageLabel(task);
  clipProgressPercent.textContent = `${displayPercent}%`;
  const detailParts = [];
  if (task.status === "failed") {
    detailParts.push(String(task.error || task.message || "截取失败。"));
  } else {
    detailParts.push(String(task.message || "正在截取片段。"));
    if (Number(task.total_seconds) > 0) {
      detailParts.push(`${formatClock(task.completed_seconds || 0)} / ${formatClock(task.total_seconds || 0)}`);
    }
    if (task.status === "completed" && task.result_project_name) {
      detailParts.push(`已创建项目：${String(task.result_project_name)}`);
    }
  }
  clipProgressDetail.textContent = detailParts.join(" · ");
  if (terminal) {
    clipCancelButton.disabled = false;
    clipSubmitButton.disabled = false;
    clipSubmitButton.textContent = "确认";
  }
}

function renderClipLockState() {
  const clipping = clipTaskRunning();
  fileInput.disabled = clipping;
  fileInput.closest(".upload-button")?.classList.toggle("disabled", clipping);
  projectList.classList.toggle("disabled", clipping);
  if (clipping) {
    hideProjectContextMenu();
  }
}

function showClipDialog(projectId) {
  state.clipDialogProjectId = projectId;
  clipStartInput.value = "00:00.000";
  clipEndInput.value = "03:00.000";
  clipError.hidden = true;
  clipError.textContent = "";
  if (!clipTaskRunning()) {
    state.clipTask = null;
  }
  renderClipDialogState();
  if (!clipDialog.open) {
    clipDialog.showModal();
  }
}

function ensureClipDialogVisibility() {
  if (!clipTaskRunning()) {
    return;
  }
  state.clipDialogProjectId = state.clipTask?.source_project_id || state.clipDialogProjectId || state.currentProjectId;
  renderClipDialogState();
  if (!clipDialog.open) {
    clipDialog.showModal();
  }
}

function parseClockInput(value) {
  const text = String(value || "").trim();
  if (!text) {
    throw new Error("时间不能为空。");
  }
  const parts = text.split(":");
  if (parts.length < 2 || parts.length > 3) {
    throw new Error("时间格式应为 分:秒.毫秒 或 时:分:秒.毫秒。");
  }
  const secondsValue = Number(parts[parts.length - 1]);
  const minutesValue = Number(parts[parts.length - 2]);
  const hoursValue = parts.length === 3 ? Number(parts[0]) : 0;
  if (![secondsValue, minutesValue, hoursValue].every((item) => Number.isFinite(item))) {
    throw new Error("时间格式无效。");
  }
  return (hoursValue * 3600) + (minutesValue * 60) + secondsValue;
}

async function handleProjectContextAction(action, projectId) {
  if (!projectId) {
    return;
  }
  if (clipTaskRunning()) {
    return;
  }
  if (action === "show-review") {
    const visibilityMap = {
      ...(state.sidebarState.source_review_visibility_by_project || {}),
      [projectId]: true,
    };
    await persistSidebarState({ source_review_visibility_by_project: visibilityMap });
    renderAll();
    return;
  }
  if (action === "hide-review") {
    const visibilityMap = {
      ...(state.sidebarState.source_review_visibility_by_project || {}),
      [projectId]: false,
    };
    await persistSidebarState({ source_review_visibility_by_project: visibilityMap });
    renderAll();
    return;
  }
  if (action === "export") {
    window.location.href = `/api/projects/${encodeURIComponent(projectId)}/export`;
    return;
  }
  if (action === "clip") {
    showClipDialog(projectId);
    return;
  }
  if (action === "stop") {
    const project = state.projects.find((item) => item.id === projectId);
    if (!window.confirm(`确认停止「${project?.name || projectId}」的当前任务？\n当前进行中的转写、纠错或切段都会被终止。`)) {
      return;
    }
    await request(`/api/projects/${encodeURIComponent(projectId)}/stop`, { method: "POST" });
    await refreshAppState();
    return;
  }
  if (action === "clear") {
    const project = state.projects.find((item) => item.id === projectId);
    if (!window.confirm(`确认清空「${project?.name || projectId}」吗？\n会保留项目配置和原始视频，其余产物都会删除。`)) {
      return;
    }
    await request(`/api/projects/${encodeURIComponent(projectId)}/clear`, { method: "POST" });
    await refreshAppState();
    return;
  }
  if (action === "delete") {
    const project = state.projects.find((item) => item.id === projectId);
    if (!window.confirm(`确认删除「${project?.name || projectId}」吗？\n项目目录会被彻底删除。`)) {
      return;
    }
    await request(`/api/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
    if (state.currentProjectId === projectId) {
      state.currentProjectId = null;
      state.detail = null;
    }
    await refreshAppState();
  }
}

async function submitClipDialog() {
  const projectId = state.clipDialogProjectId;
  if (!projectId) {
    return;
  }
  try {
    clipError.hidden = true;
    clipError.textContent = "";
    const startSeconds = parseClockInput(clipStartInput.value);
    const endSeconds = parseClockInput(clipEndInput.value);
    if (endSeconds <= startSeconds) {
      throw new Error("结束时间必须大于开始时间。");
    }
    const payload = await request(
      `/api/projects/${encodeURIComponent(projectId)}/clip-test?start_seconds=${encodeURIComponent(startSeconds)}&duration_seconds=${encodeURIComponent(endSeconds - startSeconds)}`,
      { method: "POST" }
    );
    state.clipTask = payload.task || null;
    renderAll();
    ensureClipDialogVisibility();
    startClipPolling();
  } catch (error) {
    clipError.hidden = false;
    clipError.textContent = String(error.message || error);
  }
}

function attachEventHandlers() {
  modelSettings.addEventListener("change", async (event) => {
    if (clipTaskRunning()) {
      return;
    }
    const select = event.target.closest("select[data-setting-key]");
    if (!select) {
      return;
    }
    const originalValue = state.systemStatus?.settings?.[select.dataset.settingKey] || select.value;
    try {
      select.disabled = true;
      jobMessage.textContent = "正在更新模型设置，新任务会使用新配置。";
      await updateRuntimeSetting(select.dataset.settingKey, select.value);
      renderModelSettings();
      jobMessage.textContent = "模型设置已更新，新任务将使用新配置。";
    } catch (error) {
      select.value = originalValue;
      window.alert(String(error.message || error));
      await refreshAppState({ includeSystemStatus: true });
    } finally {
      select.disabled = false;
    }
  });

  sourceLanguageSelect.addEventListener("change", async () => {
    if (clipTaskRunning()) {
      return;
    }
    try {
      await updateProjectLanguage(sourceLanguageSelect.value);
    } catch (error) {
      window.alert(String(error.message || error));
      await refreshAppState();
    }
  });

  targetLanguageSelect?.addEventListener("change", () => {
    if (clipTaskRunning()) {
      return;
    }
    updateTargetLanguage(targetLanguageSelect.value).catch(async (error) => {
      window.alert(String(error.message || error));
      await refreshAppState();
    });
  });

  fileInput.addEventListener("change", async (event) => {
    if (clipTaskRunning()) {
      event.target.value = "";
      return;
    }
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) {
      return;
    }
    try {
      await importVideo(file);
    } catch (error) {
      window.alert(`导入失败：${String(error.message || error)}`);
    }
  });

  startSourceProcessButton.addEventListener("click", async () => {
    if (clipTaskRunning()) {
      return;
    }
    try {
      await startSourceProcess();
    } catch (error) {
      window.alert(`转写分段失败：${String(error.message || error)}`);
      await refreshAppState();
    }
  });

  startTargetProcessButton?.addEventListener("click", async () => {
    if (clipTaskRunning()) {
      return;
    }
    try {
      await startTargetProcess();
    } catch (error) {
      window.alert(`翻译合成失败：${String(error.message || error)}`);
      await refreshAppState();
    }
  });

  startFullProcessButton?.addEventListener("click", async () => {
    if (clipTaskRunning()) {
      return;
    }
    try {
      await startFullProcess();
    } catch (error) {
      window.alert(`转写合成失败：${String(error.message || error)}`);
      await refreshAppState();
    }
  });

  sourceSegments.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-segment-action], button[data-review-action]");
    if (!button) {
      return;
    }
    const segmentId = button.dataset.segmentId;
    try {
      if (button.dataset.segmentAction === "play") {
        await playSegmentAudio(segmentId);
      } else if (button.dataset.segmentAction === "jump") {
        jumpToSegment(segmentId);
      } else if (button.dataset.segmentAction === "save") {
        await saveSegmentText(segmentId);
      } else if (button.dataset.reviewAction === "accept") {
        await applyReviewAction(segmentId, "accept");
      } else if (button.dataset.reviewAction === "reject") {
        await applyReviewAction(segmentId, "reject");
      }
    } catch (error) {
      window.alert(String(error.message || error));
      await refreshAppState();
    }
  });

  sourceSegments.addEventListener("change", (event) => {
    const checkbox = event.target.closest("[data-source-review-apply-all]");
    if (!checkbox) {
      return;
    }
    state.bulkPendingEnabled = checkbox.checked;
    sourceSegments.querySelectorAll("[data-source-review-apply-all]").forEach((node) => {
      node.checked = state.bulkPendingEnabled;
    });
  });

  sourceSegments.addEventListener("scroll", () => {
    if (shouldIgnoreSuppressedScroll("source", sourceSegments.scrollTop)) {
      return;
    }
    rememberSegmentScrollState(sourceSegments.scrollTop, targetSegments?.scrollTop || 0);
    scheduleScrollStatePersist();
    if (!state.isSyncingScroll) {
      scheduleScrollSync("source");
    }
    hideTextContextMenu();
  }, { passive: true });

  sourceSegments.addEventListener("wheel", () => {
    noteScrollGesture("source");
  }, { passive: true });

  sourceSegments.addEventListener("pointerdown", () => {
    noteScrollGesture("source");
  }, { passive: true });

  targetSegments?.addEventListener("scroll", () => {
    if (shouldIgnoreSuppressedScroll("target", targetSegments.scrollTop)) {
      return;
    }
    rememberSegmentScrollState(sourceSegments?.scrollTop || 0, targetSegments.scrollTop);
    scheduleScrollStatePersist();
    if (!state.isSyncingScroll) {
      scheduleScrollSync("target");
    }
    hideTextContextMenu();
  }, { passive: true });

  targetSegments?.addEventListener("wheel", () => {
    noteScrollGesture("target");
  }, { passive: true });

  targetSegments?.addEventListener("pointerdown", () => {
    noteScrollGesture("target");
  }, { passive: true });

  targetSegments?.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-target-segment-action]");
    if (!button) {
      return;
    }
    try {
      if (button.dataset.targetSegmentAction === "play") {
        await playSegmentAudio(button.dataset.segmentId, "target");
      } else if (button.dataset.targetSegmentAction === "save") {
        await saveTargetSegmentText(button.dataset.segmentId);
      }
    } catch (error) {
      window.alert(String(error.message || error));
      await refreshAppState();
    }
  });

  document.addEventListener("click", (event) => {
    if (!projectContextMenu.hidden && !projectContextMenu.contains(event.target)) {
      hideProjectContextMenu();
    }
    if (!textContextMenu.hidden && !textContextMenu.contains(event.target)) {
      hideTextContextMenu();
    }
  });

  projectContextMenu.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-context-action]");
    if (!button) {
      return;
    }
    const projectId = state.projectContextProjectId || state.currentProjectId;
    hideProjectContextMenu();
    try {
      await handleProjectContextAction(button.dataset.contextAction, projectId);
    } catch (error) {
      window.alert(String(error.message || error));
      await refreshAppState();
    }
  });

  textContextMenu.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-text-action]");
    if (!button || !state.textContextTarget) {
      return;
    }
    if (button.dataset.textAction === "select-all") {
      state.textContextTarget.focus();
      state.textContextTarget.select();
    }
    if (button.dataset.textAction === "copy") {
      const target = state.textContextTarget;
      const value = target.selectionStart !== target.selectionEnd
        ? target.value.slice(target.selectionStart, target.selectionEnd)
        : target.value;
      await navigator.clipboard.writeText(value);
    }
    hideTextContextMenu();
  });

  sourceVolumeSlider.addEventListener("input", () => {
    writeLocalPageState({ sourceVolume: Number(sourceVolumeSlider.value) });
    syncPlaybackSettings();
  });

  targetVolumeSlider?.addEventListener("input", () => {
    writeLocalPageState({ targetVolume: Number(targetVolumeSlider.value) });
    syncPlaybackSettings();
  });

  sourceSpeedSelect.addEventListener("change", () => {
    writeLocalPageState({ sourceSpeed: sourceSpeedSelect.value });
    syncPlaybackSettings();
  });

  targetSpeedSelect?.addEventListener("change", () => {
    writeLocalPageState({ targetSpeed: targetSpeedSelect.value });
    syncPlaybackSettings();
  });

  subtitleSyncToggle?.addEventListener("change", () => {
    writeLocalPageState({ subtitleSync: subtitleSyncToggle.checked });
    if (subtitleSyncEnabled()) {
      scheduleScrollSync("source");
      updateCaptionState("source");
      updateCaptionState("target");
    }
  });

  document.querySelectorAll(".controls").forEach((controlsNode) => controlsNode.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) {
      return;
    }
    const action = button.dataset.action;
    const isTarget = button.dataset.player === "target";
    const video = isTarget ? targetVideo : sourceVideo;
    if (action === "play") {
      if (!(isTarget && state.currentClipAudioSide === "target")) {
        stopCurrentClipAudio();
      }
      clearSegmentPlayback(isTarget ? "target" : "source");
      await video.play().catch(() => {});
      if (isTarget) {
        await playCurrentClipAudio("target");
      }
    }
    if (action === "pause") {
      clearSegmentPlayback(isTarget ? "target" : "source");
      video.pause();
      if (isTarget) {
        pauseCurrentClipAudio("target");
      }
    }
    if (action === "stop") {
      clearSegmentPlayback(isTarget ? "target" : "source");
      video.pause();
      video.currentTime = 0;
      stopCurrentClipAudio();
    }
    if (action === "replay") {
      const activeSegment = state.segmentPlayback[isTarget ? "target" : "source"];
      if (!(isTarget && state.currentClipAudioSide === "target")) {
        stopCurrentClipAudio();
      }
      clearSegmentPlayback(isTarget ? "target" : "source");
      video.currentTime = Math.max(0, Number(activeSegment?.start || 0));
      await video.play().catch(() => {});
      if (isTarget) {
        if (state.currentClipAudioSide === "target" && state.currentClipAudio) {
          state.currentClipAudio.currentTime = 0;
        }
        await playCurrentClipAudio("target");
      }
    }
  }));

  clipForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitClipDialog();
  });

  clipCancelButton.addEventListener("click", async () => {
    if (clipTaskRunning()) {
      return;
    }
    clipDialog.close();
    if (clipTaskTerminal()) {
      try {
        await request("/api/clip-task", { method: "DELETE" });
      } catch (_error) {
        // Ignore cleanup failures; the next dialog open will reset terminal state.
      }
      state.clipTask = null;
      renderAll();
    }
  });

  clipDialog.addEventListener("cancel", (event) => {
    if (clipTaskRunning()) {
      event.preventDefault();
    }
  });

  clipDialog.addEventListener("close", async () => {
    if (clipTaskRunning()) {
      ensureClipDialogVisibility();
      return;
    }
    if (clipTaskTerminal()) {
      try {
        await request("/api/clip-task", { method: "DELETE" });
      } catch (_error) {
        // Ignore cleanup failures; the next refresh will reset the terminal task.
      }
      state.clipTask = null;
      renderAll();
    }
  });

  projectList.addEventListener("scroll", () => {
    window.clearTimeout(projectList._scrollTimer);
    projectList._scrollTimer = window.setTimeout(() => {
      persistSidebarState({ project_list_scroll_top: projectList.scrollTop }).catch(() => {});
    }, 120);
  });

  window.addEventListener("resize", () => {
    window.requestAnimationFrame(() => {
      syncSegmentRowHeights();
    });
  });

  window.addEventListener("beforeunload", persistViewportStateBeforeUnload);
  window.addEventListener("pagehide", persistViewportStateBeforeUnload);
  window.addEventListener("pageshow", () => {
    state.isPageUnloading = false;
  });
}

async function pollCurrentProject() {
  try {
    captureLiveSegmentScrollState();
    await loadClipTask();
    await loadProjects();
    ensureSelectedProject();
    if (state.currentProjectId) {
      await loadCurrentProjectDetail();
    } else {
      state.detail = null;
    }
    renderAll();
    if (clipTaskRunning()) {
      ensureClipDialogVisibility();
      startClipPolling();
    }
  } catch (_error) {
    // Keep the current UI if polling fails transiently.
  }
}

function startPolling() {
  window.clearInterval(state.pollTimer);
  state.pollTimer = window.setInterval(() => {
    pollCurrentProject();
  }, PROJECT_POLL_INTERVAL_MS);
}

async function init() {
  attachEventHandlers();
  await loadSidebarState();
  await refreshAppState({ includeSystemStatus: true });
  startPolling();
}

void init();
