/**
 * 三态裁决解析与校验（SPEC §6）。
 *
 * 为什么要有这个文件：验收官是模型，它会（实测会）在散文里写「基本可以」「我觉得没问题」，
 * 也会输出**坏 JSON**（本项目 116 份真实裁决里就有 3 份字符串引号没转义）。
 * 所以「裁决」不能从自然语言里读 —— 只认**结构化字段**，读不出来一律降级 `escalate`。
 *
 * 三条硬规则（少一条就变成「自证通过」）：
 * 1. 解析失败 / 没有 `verdict` 字段 → `escalate`（不是 fail，更不是 pass）。
 * 2. `verdict === 'pass'` 但 `blocking` 非空 → 强制改 `rework`（自相矛盾）。
 * 3. `findings` 里 `evidence` 为空的那条 → 标 `type: 'E0'`，并在 `blocking` 追加一条「证据缺失」。
 *    （依据 `docs/REVIEW-CONTRACT.md` §二·五·1：没声明强度 = 按 E0 处理；E0 = 只有结论，什么都不支撑。）
 *
 * 兼容两种真实产出形状：
 * - SPEC §3.2 的 `findings: [{ id, claim, evidence, type: 'E0'..'E3' }]`
 * - 本项目历史裁决（`docs/decisions/*.json`）的 `criteria: [{ id, type, status, claimed_strength, evidence }]`
 *   两者的差别在 normalizeVerdict 里吸收，工具层拿到的永远是第一种。
 *
 * @module
 */

/** 只允许三态。`fail` 不是裁决 —— 它要么是 `rework`（工人工得动），要么是 `escalate`（人得介入）。 */
export const VERDICTS = Object.freeze(['pass', 'rework', 'escalate']);

/** 证据强度四档（`docs/REVIEW-CONTRACT.md` §二·五）。 */
export const EVIDENCE_TYPES = Object.freeze(['E0', 'E1', 'E2', 'E3']);

/** 一条 finding 的结论状态（历史 `criteria[].status` 用；本插件不据此改裁决，只透传）。 */
export const FINDING_STATUS = Object.freeze(['pass', 'fail', 'unverified']);

/** 解析不出结构化裁决时写进 `blocking` 的那句话（不写成空数组 —— 空数组看起来像「没意见」）。 */
export const NO_VERDICT_BLOCKING =
  '无法解析出结构化裁决：验收官没有给出带 verdict 字段的 JSON 块。只认结构化字段，散文里的"通过/没问题"不算裁决。';

/** 扫描裸 JSON 对象时的上限度：文本再长也不该把一轮请求拖成 CPU 饿死。 */
const MAX_BARE_SCAN = 4096;
/** `blocking` / `claim` 落进字符串前的长度上限（防止把整段报告塞进一条 blocking）。 */
const MAX_TEXT = 4000;

/** @returns {boolean} 是不是朴素对象（不是数组、不是 null）。 */
function isPlainObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** 安全的字符串化：非字符串一律 ''，不产生 `[object Object]`。 */
function asText(value) {
  return typeof value === 'string' ? value.trim() : '';
}

/** 截断长文本（保留尾部省略标记，让人知道被截过）。 */
function shrink(text, max = MAX_TEXT) {
  const s = String(text ?? '');
  return s.length <= max ? s : `${s.slice(0, max)}…（已截断，原长 ${s.length} 字符）`;
}

/** 大小写无关地取一个键的值（模型有时写 `Verdict`）。 */
function pickKey(obj, name) {
  const lower = name.toLowerCase();
  for (const key of Object.keys(obj)) {
    if (key.toLowerCase() === lower) return obj[key];
  }
  return undefined;
}

/** 大小写无关地判断某个键在不在。 */
function hasKey(obj, name) {
  const lower = name.toLowerCase();
  return Object.keys(obj).some((key) => key.toLowerCase() === lower);
}

/** 尝试 `JSON.parse`，失败返回 `null`（不抛）。 */
function tryJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/**
 * 从 `start` 处的 `{` 开始找配平的 JSON 对象。
 * 必须自己扫（不能用正则）：`evidence` 里常有 `{`、`}`、引号。
 */
function balancedObjectAt(text, start) {
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = start; i < text.length; i += 1) {
    const ch = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === '{') depth += 1;
    else if (ch === '}') {
      depth -= 1;
      if (depth === 0) return text.slice(start, i + 1);
    }
  }
  return null;
}

/**
 * 收集文本里所有「像裁决」的 JSON 对象候选，按出现顺序。
 * 顺序 = 围栏块 → 整段 → 裸对象扫描；后面的候选优先（验收官常在末尾给最终结论）。
 */
function candidates(text) {
  const found = [];
  const fence = /```[a-zA-Z0-9_-]*\s*\n([\s\S]*?)```/g;
  let match;
  while ((match = fence.exec(text)) !== null) {
    const body = tryJson(match[1].trim());
    if (isPlainObject(body)) found.push(body);
  }
  const whole = tryJson(text.trim());
  if (isPlainObject(whole)) found.push(whole);
  let scanned = 0;
  for (let i = 0; i < text.length && scanned < MAX_BARE_SCAN; i += 1) {
    if (text[i] !== '{') continue;
    scanned += 1;
    const raw = balancedObjectAt(text, i);
    if (!raw) continue;
    const obj = tryJson(raw);
    if (isPlainObject(obj)) found.push(obj);
  }
  return found;
}

/**
 * 解析验收官原文里的结构化裁决。
 *
 * @param {unknown} text 验收官返回的原始文本。
 * @returns {{ verdict: unknown, confidence: unknown, blocking: unknown, findings: unknown, source: string } | null}
 *   `null` 表示**没有**可解析的结构化裁决 —— 调用方必须走 `normalizeVerdict(null)` 降级为 `escalate`。
 *   返回体里的字段是**原样**的（合法性由 `normalizeVerdict` 判），只把历史别名映射到四个标准键上。
 */
export function parseVerdict(text) {
  if (typeof text !== 'string' || text.trim() === '') return null;
  // 去 BOM：Windows 侧另存/PowerShell 重定向写出来的文件常带 BOM，`JSON.parse` 会直接失败。
  // （同样的处理在 `engine/dag-gate.mjs:40` 里已有先例。）
  const body = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
  const found = candidates(body);
  // 从后往前取第一个带 verdict 键的对象：模型可能先复述、后给结论。
  for (let i = found.length - 1; i >= 0; i -= 1) {
    const obj = found[i];
    if (!hasKey(obj, 'verdict')) continue;
    return {
      verdict: pickKey(obj, 'verdict'),
      confidence: pickKey(obj, 'confidence'),
      // 历史产出里 blocking 是对象数组（{id, what, why, evidence}），这里不动它
      blocking: pickKey(obj, 'blocking'),
      // SPEC 用 findings，历史产出用 criteria —— 两种都收
      findings: hasKey(obj, 'findings') ? pickKey(obj, 'findings') : pickKey(obj, 'criteria'),
      source: i === found.length - 1 ? 'last' : 'block',
    };
  }
  return null;
}

/** 把一条 blocking 统一成字符串；对象形态（历史产出）要把 what/why/evidence 拼进去。 */
function blockingToText(item, index) {
  if (typeof item === 'string') return shrink(item.trim());
  if (isPlainObject(item)) {
    const id = asText(item.id) || `#${index + 1}`;
    const what =
      asText(item.what) || asText(item.claim) || asText(item.msg) || asText(item.message) || '（未写明问题）';
    const why = asText(item.why) || asText(item.reason);
    const evidence = asText(item.evidence);
    const parts = [`[${id}] ${what}`];
    if (why) parts.push(`理由：${why}`);
    // 没给证据就明说 —— 「每条 blocking 必须带证据引用」是契约，缺了要在结果里看得见
    parts.push(evidence ? `证据：${evidence}` : '证据：（未给出）');
    return shrink(parts.join(' ｜ '));
  }
  return shrink(String(item));
}

/** 把一条 finding 统一成 `{ id, claim, evidence, type }`；强度缺失按 E0，证据为空一律 E0。 */
function findingToObject(item, index) {
  if (!isPlainObject(item)) {
    return { id: `F${index + 1}`, claim: shrink(String(item ?? '')), evidence: '', type: 'E0' };
  }
  const id = asText(item.id) || `F${index + 1}`;
  const claim =
    asText(item.claim) ||
    asText(item.what) ||
    asText(item.title) ||
    asText(item.summary) ||
    asText(item.description) ||
    asText(item.status);
  const evidence = shrink(asText(item.evidence));
  // SPEC 形状：type 直接是 E0..E3；历史形状：type 是 fact-consistent/executable/judgment，强度在 claimed_strength
  const declaredType = asText(item.type).toUpperCase();
  const declaredStrength = asText(item.claimed_strength).toUpperCase();
  let type = 'E0';
  if (EVIDENCE_TYPES.includes(declaredType)) type = declaredType;
  else if (EVIDENCE_TYPES.includes(declaredStrength)) type = declaredStrength;
  // E0 的定义就是「只有结论，没有证据」—— 所以证据为空时不管它自报几档，都按 E0
  if (!evidence) type = 'E0';
  return { id, claim: shrink(claim), evidence, type };
}

/** `confidence` 归一化到 [0, 1]；拿不到数值就记 0（不编一个「看起来很可信」的数）。 */
function coerceConfidence(value, notes) {
  const num = typeof value === 'number' ? value : typeof value === 'string' ? Number(value.trim()) : NaN;
  if (!Number.isFinite(num)) {
    notes.push('confidence 缺失或不是数值，按 0 记');
    return 0;
  }
  if (num < 0 || num > 1) {
    notes.push(`confidence=${num} 超出 0–1，已夹到边界`);
    return Math.min(1, Math.max(0, num));
  }
  return num;
}

/**
 * 不合规时降级。**绝不默认 pass**。
 *
 * @param {ReturnType<typeof parseVerdict>} parsed `parseVerdict` 的返回值（可以是 `null`）。
 * @returns {{ verdict: 'pass'|'rework'|'escalate', confidence: number, blocking: string[], findings: object[], notes: string[] }}
 *   `notes` 记录降级原因，供落盘证据用（工具返回值不带它）。
 */
export function normalizeVerdict(parsed) {
  const notes = [];
  if (!isPlainObject(parsed)) {
    return {
      verdict: 'escalate',
      confidence: 0,
      blocking: [NO_VERDICT_BLOCKING],
      findings: [],
      notes: ['no-structured-verdict'],
    };
  }

  // ── verdict ────────────────────────────────────────────────────────────────
  const rawVerdict = asText(pickKey(parsed, 'verdict')).toLowerCase();
  let verdict;
  if (VERDICTS.includes(rawVerdict)) {
    verdict = rawVerdict;
  } else {
    verdict = 'escalate';
    notes.push(`verdict 非法（${JSON.stringify(sinkValue(pickKey(parsed, 'verdict')))}）→ escalate`);
  }

  // ── findings ───────────────────────────────────────────────────────────────
  const rawFindings = pickKey(parsed, 'findings');
  const findingList = Array.isArray(rawFindings) ? rawFindings : [];
  if (rawFindings !== undefined && !Array.isArray(rawFindings)) {
    notes.push('findings 不是数组，按空数组处理');
  }
  const findings = findingList.map(findingToObject);

  // ── blocking ───────────────────────────────────────────────────────────────
  const rawBlocking = pickKey(parsed, 'blocking');
  const blocking = [];
  if (Array.isArray(rawBlocking)) {
    rawBlocking.forEach((item, index) => {
      const text = blockingToText(item, index);
      if (text) blocking.push(text);
    });
  } else if (rawBlocking !== undefined && rawBlocking !== null) {
    notes.push('blocking 不是数组，已忽略');
  }

  // ── 规则：证据为空的 finding → E0 + 追加「证据缺失」────────────────────────
  for (const finding of findings) {
    if (finding.evidence) continue;
    blocking.push(
      `证据缺失：[${finding.id}] ${finding.claim || '（未写明主张）'} —— 只有结论，没有 file:line 或命令输出，按 E0 处理`,
    );
    notes.push(`finding ${finding.id} 证据为空 → E0`);
  }

  // ── 规则：pass 但 blocking 非空 → rework（自相矛盾）───────────────────────
  if (verdict === 'pass' && blocking.length > 0) {
    verdict = 'rework';
    notes.push(`verdict=pass 但 blocking 有 ${blocking.length} 条 → rework`);
  }

  return {
    verdict,
    confidence: coerceConfidence(pickKey(parsed, 'confidence'), notes),
    blocking,
    findings,
    notes,
  };
}

/** 把非法值塞进提示串时先脱敏成短字符串，避免把整个对象打印出来。 */
function sinkValue(value) {
  if (value === undefined) return '(缺失)';
  if (typeof value === 'string') return shrink(value, 120);
  if (value === null) return null;
  if (typeof value === 'object') return '[object]';
  return value;
}

/**
 * 最后一道闸：形状不对就抛错（工具失败，模型看得见）。
 * 用途：确保「已经过 normalize 的裁决」真的合规 —— 任何一处漏归一化都会在这里炸，而不是静默通过。
 *
 * @param {unknown} value 待校验的裁决。
 * @returns {object} 原对象（校验通过）。
 * @throws {Error} 任一字段不合规时。
 */
export function assertVerdict(value) {
  const bad = (msg) => {
    throw new Error(`裁决不合规：${msg}`);
  };
  if (!isPlainObject(value)) bad('裁决必须是对象');
  if (!VERDICTS.includes(value.verdict)) {
    bad(`verdict 必须是 ${VERDICTS.join(' / ')} 之一，实际是 ${JSON.stringify(sinkValue(value.verdict))}`);
  }
  if (typeof value.confidence !== 'number' || !Number.isFinite(value.confidence)) {
    bad(`confidence 必须是数字，实际是 ${JSON.stringify(sinkValue(value.confidence))}`);
  }
  if (value.confidence < 0 || value.confidence > 1) bad(`confidence 必须在 0–1，实际是 ${value.confidence}`);
  if (!Array.isArray(value.blocking) || value.blocking.some((item) => typeof item !== 'string')) {
    bad('blocking 必须是字符串数组（对象形态应在 normalizeVerdict 里拼成字符串）');
  }
  if (!Array.isArray(value.findings)) bad('findings 必须是数组');
  value.findings.forEach((finding, index) => {
    const at = `findings[${index}]`;
    if (!isPlainObject(finding)) bad(`${at} 必须是对象`);
    if (typeof finding.id !== 'string' || finding.id === '') bad(`${at}.id 必须是非空字符串`);
    if (typeof finding.claim !== 'string') bad(`${at}.claim 必须是字符串`);
    if (typeof finding.evidence !== 'string') bad(`${at}.evidence 必须是字符串（证据为空就留空串）`);
    if (!EVIDENCE_TYPES.includes(finding.type)) {
      bad(`${at}.type 必须是 ${EVIDENCE_TYPES.join(' / ')} 之一，实际是 ${JSON.stringify(sinkValue(finding.type))}`);
    }
  });
  if (value.verdict === 'pass' && value.blocking.length > 0) {
    bad('verdict=pass 但 blocking 非空（自相矛盾；normalizeVerdict 应已降级为 rework）');
  }
  return value;
}
