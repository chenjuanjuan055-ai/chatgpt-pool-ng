// 空配置：阻止 postcss 向上层目录搜索，避免误用 D:\xi\Documents\Ai01\postcss.config.js（tailwind 3 插件）。
// 样式由 @tailwindcss/vite 插件处理，这里不需要任何 postcss 插件。
export default { plugins: {} };
