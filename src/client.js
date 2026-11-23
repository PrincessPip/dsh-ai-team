/**
 * dsh-ai-team — browser half（手写 ModuleLoader bundle，无构建步骤）。
 *
 * 只有一个控件：输入框左侧工具行里、权限控件**右边**的「蜂群 ON / 蜂群 OFF」两态开关。
 * 它本身不决定任何行为，只写 Host 侧插件的 config 字段 `swarmMode`
 * （`src/index.js` 里声明为 `volatile`，所以写入立刻进运行中的引用，下一轮 system prompt 生效）。
 *
 * 四条硬约束（踩过的）：
 * 1. **不能占 `conversation.input.permission` 槽**：那个槽在契约里是 `kind: 'single'`
 *    （`dsh-client-ui-conversation/lib/types/client/contract/slots.d.ts:345`），
 *    单占位、只渲染一个 occupant —— 注册进去会**把官方权限控件顶掉**（实测：整颗选择器消失）。
 *    本开关用 `conversation.input.left`（`kind: 'list'`、无 owner），它在权限控件那一行之后渲染，
 *    视觉上就在权限右边，且允许多个 occupant 共存。
 * 2. **不硬编码底色**：关闭态用 `transparent` + `--dsw-alias-border-l4` 描边，浅色下就是白底黑字，
 *    暗色主题自动变深底浅字；写死 `#fff` 会在暗色主题下变成一块白斑。
 * 3. **品牌蓝只能取静态色**：`--dsw-alias-brand-primary` 实测是中性黑白（浅 `bluish-1000` / 暗 `bluish-50`），
 *    不是蓝；蓝底用 `--dsw-static-deepseek-450`、悬停 `-500`，文字配白。
 * 4. **状态走 `configForms`**：那是 Host 侧 settings 文档的客户端读/写通道；
 *    没有 `settingsScope` 这种服务（外部插件曾误用，页面永远停在「读取中」）。
 */
window.__ModuleLoader__.load({
  id: "dsh-ai-team",
  factory: function (require) {
    var module = { exports: {} }
    var exports = module.exports
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" })

    var React = require("react")
    var E = React.createElement
    var useState = React.useState
    var useEffect = React.useEffect

    var TAG = "[ai-team/client]"
    /** locale 命名空间（`ctx.locale.register` 的 key）。 */
    var NS = "aiTeam.swarm"
    /** Host 侧插件行 id —— 也是 configForms 的 entryId / settings 的 section 名。 */
    var ENTRY_ID = "dsh-ai-team"
    /** 字段名，与 `src/index.js` 的 `Config.swarmMode` 必须逐字一致。 */
    var FIELD = "swarmMode"
    var STYLE_ID = "dsh-ai-team-style"
    /** `kind: 'list'`、`scope: 'session'`、无 owner：渲染在权限控件那一行之后。 */
    var SLOT = "conversation.input.left"
    /**
     * 同槽按 `order` **升序**渲染、缺省 `0`。
     *
     * 取 **-200**（负值）的理由：list 槽的第一个位置才是「紧贴 permission + plan」。
     * 已知占位：`dsh-voice-local` 的麦克风按钮用 `order: -100`；比它小一位就排在它左边，
     * 于是输入框左侧的顺序是 `权限 → 蜂群 → 语音`。
     * 兼容性：别的环境若没装语音插件，本开关仍在最前＝紧贴权限控件；若有人用了更小的 order，
     * 只会让本开关往右挪一格，不会挤掉任何人的控件。
     */
    var SLOT_ORDER = -200

    var zh = {
      on: "蜂群 ON",
      off: "蜂群 OFF",
      onHint: "蜂群模式已开启：Lead 只调度，实现类任务必须走 ai_team_plan → gate → run。点击关闭。",
      offHint: "蜂群模式已关闭：Lead 会直接动手。点击开启，让 Lead 降为调度员。",
      loading: "正在读取蜂群模式设置…",
      saveFailed: "保存失败",
      unavailable: "蜂群模式开关不可用：Host 没有提供 ai-team 的配置表单",
    }

    var en = {
      on: "Swarm ON",
      off: "Swarm OFF",
      onHint: "Swarm mode is ON: the Lead only dispatches; implementation work must go through ai_team_plan → gate → run. Click to turn off.",
      offHint: "Swarm mode is OFF: the Lead implements directly. Click to turn the Lead into a dispatcher.",
      loading: "Reading the swarm-mode setting…",
      saveFailed: "Could not save",
      unavailable: "Swarm toggle unavailable: the Host serves no ai-team configuration form",
    }

    var CSS = [
      ".dsh-ai-team-swarm{appearance:none;font:inherit;font-size:13px;font-weight:500;line-height:20px;height:28px;padding:0 10px;border-radius:var(--dsw-radius-sm);border:1px solid transparent;background:transparent;color:var(--dsw-alias-label-secondary);cursor:pointer;display:inline-flex;align-items:center;gap:4px;white-space:nowrap}",
      ".dsh-ai-team-swarm:hover:not(:disabled){background:var(--dsw-alias-interactive-bg-hover)}",
      ".dsh-ai-team-swarm[data-on='true']{background:var(--dsw-static-deepseek-450,#5686fe);color:#fff}",
      ".dsh-ai-team-swarm[data-on='true']:hover:not(:disabled){background:var(--dsw-static-deepseek-500,#4176e6)}",
      ".dsh-ai-team-swarm[data-on='false']{border-color:var(--dsw-alias-border-l4)}",
      ".dsh-ai-team-swarm:disabled{opacity:.55;cursor:default}",
      ".dsh-ai-team-swarm:focus-visible{outline:none;box-shadow:0 0 0 2px var(--dsw-focus-ring-color,var(--dsw-alias-state-business-primary))}",
    ].join("\n")

    function ensureStyles() {
      try {
        if (typeof document === "undefined" || typeof document.getElementById !== "function") return function () {}
        if (document.getElementById(STYLE_ID)) return function () {}
        var style = document.createElement("style")
        style.id = STYLE_ID
        style.textContent = CSS
        document.head.appendChild(style)
        return function () {
          try { if (style.parentNode) style.parentNode.removeChild(style) } catch (error) { /* best effort */ }
        }
      } catch (error) {
        return function () {}
      }
    }

    function fallbackT(key) {
      return zh[key] || key
    }

    function translator(locale) {
      try {
        if (locale && typeof locale.bind === "function") return locale.bind(NS)
      } catch (error) { /* fall through */ }
      return fallbackT
    }

    /** 订阅 configForms 的 snapshot；引用稳定到下次变化，可直接渲染。 */
    function useFormSnapshot(form) {
      var bumpState = useState(0)
      var bump = bumpState[1]
      useEffect(function () {
        if (!form || typeof form.subscribe !== "function") return undefined
        try {
          return form.subscribe(function () { bump(function (n) { return n + 1 }) })
        } catch (error) {
          console.warn(TAG + " subscribe failed:", error && error.message ? error.message : error)
          return undefined
        }
      }, [form])
      var snap = { status: "unavailable", value: undefined, writable: false }
      try {
        if (form && typeof form.getSnapshot === "function") snap = form.getSnapshot()
      } catch (error) { /* keep unavailable */ }
      return snap
    }

    function SwarmToggle(props) {
      var t = typeof props.t === "function" ? props.t : fallbackT
      var form = props.form
      var snap = useFormSnapshot(form)
      var busyState = useState(false)
      var busy = busyState[0]
      var setBusy = busyState[1]
      var errorState = useState("")
      var error = errorState[0]
      var setError = errorState[1]

      if (snap.status === "unavailable") return null

      var ready = snap.status === "ready"
      var value = ready && snap.value && typeof snap.value === "object" ? snap.value : {}
      var enabled = value[FIELD] === true
      var writable = snap.writable === true
      var disabled = !ready || !writable || busy
      var label = !ready ? t("loading") : enabled ? t("on") : t("off")
      var title = !ready ? t("loading") : enabled ? t("onHint") : t("offHint")
      if (error) title = title + " — " + error

      function toggle() {
        if (!form || typeof form.set !== "function") return
        setError("")
        setBusy(true)
        form.set(FIELD, !enabled).then(function (accepted) {
          setBusy(false)
          if (!accepted) setError(t("saveFailed"))
        }).catch(function (err) {
          setBusy(false)
          setError(String(err && err.message ? err.message : err))
        })
      }

      return E("button", {
        type: "button",
        className: "dsh-ai-team-swarm",
        "data-on": enabled ? "true" : "false",
        "aria-pressed": enabled ? "true" : "false",
        title: title,
        "aria-label": title,
        disabled: disabled,
        onClick: toggle,
      }, label)
    }

    exports.name = "dsh-ai-team/client"
    exports.inject = ["slots", "locale", "configForms"]

    exports.apply = function (ctx) {
      var t = translator(ctx.locale)
      var form = ctx.configForms.get(ENTRY_ID)

      ctx.effect(function () {
        var disposeStyle = ensureStyles()
        var disposeDict
        try {
          disposeDict = ctx.locale.register(NS, { zh: zh, en: en })
        } catch (error) {
          console.warn(TAG + " dictionary registration failed:", error && error.message ? error.message : error)
        }
        return function () {
          try { if (typeof disposeStyle === "function") disposeStyle() } catch (error) { /* best effort */ }
          try { if (typeof disposeDict === "function") disposeDict() } catch (error) { /* best effort */ }
        }
      }, "dsh-ai-team: styles, dictionaries")

      ctx.slots.inject(SLOT, function () {
        return ctx.slots.register({
          name: SLOT,
          id: "ai-team-swarm",
          order: SLOT_ORDER,
          locale: NS,
          inject: function () { return { form: form } },
        }, SwarmToggle)
      })
    }

    return module.exports
  },
})
