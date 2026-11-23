/**
 * 工具输出声明（`defineTool({ output })` 的那一半）。
 *
 * 照抄官方 `@deepseek-ai/dsh-experimental-tool-agent-team` 的 `jsonOutput()`：
 * 一份 canonical `schema` + 把**已校验**的值原样 JSON 化后给模型看。
 * 不自己发明渲染格式 —— 五个工具的输出结构化，模型读 JSON 比读自由文本稳。
 *
 * @param {object} schema defineTool 认的 value schema（`required` 写在属性对象**内部**）。
 * @returns {{ schema: object, render: (args: unknown, value: unknown) => { type: 'text', text: string }[] }}
 */
export function jsonOutput(schema) {
  return {
    schema,
    render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
  };
}
