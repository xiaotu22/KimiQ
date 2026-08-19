#!/usr/bin/env node
/* ============================================================
 * kimiq-hook.mjs —— Kimi Code hooks → KimiQ 桌宠 HTTP 接口转发器
 *
 * hooks 只负责发，桌宠负责演，二者解耦：
 *   config.toml 里每个事件调用本脚本，本脚本 curl 一下
 *   http://127.0.0.1:28765/state?to=<表情> 即退出。
 *
 * 用法：
 *   node kimiq-hook.mjs <表情>                     立即切换
 *   node kimiq-hook.mjs <表情> --detail            从 stdin 的事件 JSON 里抠出
 *                                                 "Kimi 正在干什么"一并发给气泡
 *   node kimiq-hook.mjs <表情> --then <表情> --delay <ms>
 *                                                 先切第一个，延迟后再切第二个
 *                                                 （用于 Stop：先 39 输出回复 → 再 33 任务完成）
 *   node kimiq-hook.mjs perm                       读 stdin 判断权限结果：
 *                                                 拒绝 → 38 拒绝受限；通过 → 32 处理中忙碌
 *   node kimiq-hook.mjs baby add|remove            子代理小球：SubagentStart/Stop 时召唤/收回
 *   node kimiq-hook.mjs autostart                  随 Kimi 自启：桌宠没在跑就拉起
 *
 * 桌宠没运行时静默成功（fail-open），绝不阻塞 Kimi 主流程。
 * ============================================================ */

const PORT = 28765;
const TIMEOUT_MS = 1500;
const HOME_JSON = (process.env.APPDATA || '') + '/KimiQ/home.json';

async function fire(to, text) {
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
    let url = `http://127.0.0.1:${PORT}/state?to=${encodeURIComponent(to)}`;
    if (text) url += `&text=${encodeURIComponent(text)}`;
    await fetch(url, { signal: ctl.signal });
    clearTimeout(timer);
  } catch {
    /* 桌宠未启动：静默 */
  }
}

/* 探活：桌宠在跑返回 true */
async function alive() {
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
    await fetch(`http://127.0.0.1:${PORT}/sound?play=none`, { signal: ctl.signal });
    clearTimeout(timer);
    return true;
  } catch {
    return false;
  }
}

/* 随 Kimi 自启：读 home.json 找本体（桌宠每次启动都会刷新它，
   仓库改名/搬家、exe 换位置都断不了） */
async function autostart() {
  if (await alive()) return;
  const { existsSync, readFileSync } = await import('node:fs');
  const { spawn } = await import('node:child_process');
  if (!existsSync(HOME_JSON)) return;
  let cfg = {};
  try { cfg = JSON.parse(readFileSync(HOME_JSON, 'utf8')); } catch { return; }
  let child = null;
  if (cfg.exe && existsSync(cfg.exe)) {
    child = spawn(cfg.exe, [], { detached: true, stdio: 'ignore' });
  } else if (cfg.repo && existsSync(cfg.repo + '/kimiq.py')) {
    child = spawn('pythonw', [cfg.repo + '/kimiq.py'],
      { detached: true, stdio: 'ignore', cwd: cfg.repo });
  }
  if (child) child.unref();
}

/* 从事件 JSON 抠"Kimi 正在干什么"：工具名+关键参数 → 一句话。
   字段名随 CLI 版本可能漂移，全部 fail-soft：抠不出就只切表情 */
function summarize(raw) {
  try {
    const ev = JSON.parse(raw);
    const tool = ev.tool_name || ev.tool || ev.name || '';
    const inp = ev.tool_input || ev.input || ev.args || ev.arguments || {};
    const base = (p) => String(p || '').split(/[\\/]/).pop();
    const cut = (s, n) => {
      s = String(s || '').trim().replace(/\s+/g, ' ');
      return s.length > n ? s.slice(0, n) + '…' : s;
    };
    switch (tool) {
      case 'TodoList': {
        const todos = inp.todos || [];
        const i = todos.findIndex((t) => t.status === 'in_progress');
        return i >= 0 ? `${i + 1}/${todos.length} · ${cut(todos[i].title, 12)}` : '';
      }
      case 'Read': return `读 ${cut(base(inp.file_path || inp.path), 14)}`;
      case 'Edit': return `改 ${cut(base(inp.file_path || inp.path), 14)}`;
      case 'Write': return `写 ${cut(base(inp.file_path || inp.path), 14)}`;
      case 'Grep': return `检索 ${cut(inp.pattern, 12)}`;
      case 'Glob': return `找 ${cut(inp.pattern, 12)}`;
      case 'Bash': return `跑 ${cut(inp.command, 12)}`;
      case 'WebSearch': return `搜 ${cut(inp.query, 12)}`;
      case 'FetchURL': return `看 ${cut(inp.url, 14)}`;
      case 'Agent': case 'AgentSwarm': return `派分身 · ${cut(inp.description, 8)}`;
      default: return '';
    }
  } catch { return ''; }
}

function readStdin() {
  return new Promise((resolve) => {
    let data = '';
    process.stdin.on('data', (c) => (data += c));
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', () => resolve(''));
    setTimeout(() => resolve(data), 800);   // 没有 stdin 时别干等
  });
}

async function main() {
  const args = process.argv.slice(2);
  if (!args.length) return;

  if (args[0] === 'perm') {
    // PermissionResult：从事件 JSON 里抠结果。字段名随版本可能变，
    // 直接对整串做关键词兜底匹配，判不出就当作"通过"。
    const raw = (await readStdin()).toLowerCase();
    const denied = /deny|denied|reject|block|refuse/.test(raw);
    await fire(denied ? '38' : '32');
    return;
  }

  if (args[0] === 'baby') {
    // SubagentStart/Stop：召唤/收回子代理小球
    const op = args[1] === 'remove' ? 'remove' : 'add';
    try {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
      await fetch(`http://127.0.0.1:${PORT}/baby?op=${op}`, { signal: ctl.signal });
      clearTimeout(timer);
    } catch { /* 静默 */ }
    return;
  }

  if (args[0] === 'autostart') {
    await autostart();
    return;
  }

  // --detail：从 stdin 抠详情（工具名/文件/命令），随状态一起发给气泡
  const detail = args.includes('--detail') ? summarize(await readStdin()) : '';
  await fire(args[0], detail);

  const iThen = args.indexOf('--then');
  if (iThen >= 0 && args[iThen + 1]) {
    const iDelay = args.indexOf('--delay');
    const delay = iDelay >= 0 ? parseInt(args[iDelay + 1], 10) || 0 : 0;
    if (delay > 0) await new Promise((r) => setTimeout(r, delay));
    await fire(args[iThen + 1]);
  }
}

main();
