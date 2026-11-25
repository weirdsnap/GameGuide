// 窗口化无限滚动的离线验证：从 hollow_knight.html 抽出真实的 methods / computed，
// 配一个假布局模型跑窗口移动，验证：
//   (a) 边界不越界  (b) 占位高度与估算/实测一致  (c) 窗口操作后视口内容不动
//   (d) 窗口有上限   (e) 短会话不出空窗
// 聊天窗口化渲染的离线逻辑测试。前端文件在博客仓库，跨仓库读取。
// 跑法：node tests/test_chat_window.js   （或 HK_HTML=<路径> node ...）
const fs = require('fs');
const path = require('path');

const HTML = process.env.HK_HTML || process.argv[2]
    || path.join(__dirname, '..', '..', 'weirdsnap.github.io', 'htmls', 'hollow_knight.html');
if (!fs.existsSync(HTML)) {
    console.error('找不到前端文件: ' + HTML + '（用 HK_HTML 环境变量指定）');
    process.exit(2);
}
const html = fs.readFileSync(HTML, 'utf8');
const js = html.match(/<script(?![^>]*\bsrc)[^>]*>([\s\S]*?)<\/script>/)[1];

function sliceBlock(src, startMarker) {
  const i = src.indexOf(startMarker);
  if (i < 0) throw new Error('marker not found: ' + startMarker);
  const start = src.indexOf('{', i);
  let depth = 0;
  for (let j = start; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') { depth--; if (depth === 0) return src.slice(start + 1, j); }
  }
  throw new Error('unbalanced');
}

// 页面里有两个组件（header 菜单 + 主聊天），要定位主 app 之后的那个
const appAt = js.indexOf('const app = Vue.createApp');
if (appAt < 0) throw new Error('main app not found');
const mainJs = js.slice(appAt);
const methodsBody = sliceBlock(mainJs, 'methods: {');
const computedBody = sliceBlock(mainJs, 'computed: {');

// 常量从源码读，避免两处漂移
const consts = {};
for (const name of ['WIN_MSG', 'STEP_MSG', 'MAX_WIN', 'EDGE_PX', 'GAP_PX', 'EST_H']) {
  const m = js.match(new RegExp('const ' + name + ' = (\\d+);'));
  if (!m) throw new Error('const not found: ' + name);
  consts[name] = Number(m[1]);
}
const argNames = Object.keys(consts);
const argVals = argNames.map((k) => consts[k]);

const methods = new Function(argNames.join(','), 'return {' + methodsBody + '};')(...argVals);
const computed = new Function(argNames.join(','), 'return {' + computedBody + '};')(...argVals);

const TURN_H = 100;                       // 每条消息 offsetHeight
const ITEM_H = TURN_H + consts.GAP_PX;    // 含 gap 的占位高度
const VIEW = 600;

function makeVm(turns) {
  const messages = [{ id: 0, role: 'assistant', content: 'welcome' }];
  for (let i = 1; i <= turns * 2; i++) {
    messages.push({ id: i, role: i % 2 ? 'user' : 'assistant', content: 'm' + i });
  }
  const vm = {
    messages,
    chunkStart: Math.max(0, messages.length - consts.WIN_MSG),
    chunkEnd: messages.length,
    hCache: {},
    _inWinOp: false,
    loading: false,
    hasNewBelow: false,
    $nextTick(fn) { fn(); },
  };
  for (const [k, fn] of Object.entries(computed)) {
    Object.defineProperty(vm, k, { get: () => fn.call(vm), configurable: true });
  }
  Object.assign(vm, methods);

  // ---- 假布局：内容 = padTop 空白 + 窗口内消息 + padBottom 空白 ----
  const box = {
    clientHeight: VIEW,
    scrollTop: 0,
    get scrollHeight() {
      return vm.padTop + vm.padBottom + (vm.chunkEnd - vm.chunkStart) * ITEM_H;
    },
    getBoundingClientRect() { return { top: 0, bottom: VIEW }; },
    querySelectorAll() {
      const out = [];
      let y = vm.padTop;
      for (let i = vm.chunkStart; i < vm.chunkEnd; i++) {
        out.push(makeNode(vm.messages[i].id, y));
        y += ITEM_H;
      }
      return out;
    },
    querySelector(sel) {
      const id = Number(sel.match(/data-mid="(\d+)"/)[1]);
      let y = vm.padTop;
      for (let i = vm.chunkStart; i < vm.chunkEnd; i++) {
        if (vm.messages[i].id === id) return makeNode(id, y);
        y += ITEM_H;
      }
      return null;
    },
  };
  function makeNode(id, y) {
    return {
      offsetHeight: TURN_H,
      getAttribute: () => String(id),
      getBoundingClientRect: () => ({ top: y - box.scrollTop, bottom: y - box.scrollTop + TURN_H }),
    };
  }
  vm.$refs = { msgBox: box };
  return vm;
}

function winSize(vm) { return vm.chunkEnd - vm.chunkStart; }
function totalH(vm) { return vm.padTop + vm.padBottom + winSize(vm) * ITEM_H; }

let fails = 0;
function check(name, cond, extra) {
  if (!cond) { fails++; console.log('FAIL ' + name + (extra === undefined ? '' : '  ' + extra)); }
  else console.log('ok   ' + name);
}

// ---------- 1. 初始状态 ----------
const vm = makeVm(30);                       // 60 条消息 + 欢迎语
check('初始窗口 = WIN_MSG 条', winSize(vm) === consts.WIN_MSG, winSize(vm));
check('初始顶部占位 = 未渲染条数 × 估算高度',
      vm.padTop === (vm.messages.length - consts.WIN_MSG) * consts.EST_H, vm.padTop);
check('初始底部无占位', vm.padBottom === 0, vm.padBottom);

// ---------- 2. 一路滚到最早 ----------
vm.measure();
let guard = 0;
while (vm.chunkStart > 0 && guard++ < 60) {
  vm.$refs.msgBox.scrollTop = vm.padTop + 10;   // 逼近渲染区顶部（不是滚过整片空白）
  vm.onScroll();
}
check('能滚到最早一条', vm.chunkStart === 0, vm.chunkStart);
check('窗口不超上限', winSize(vm) <= consts.MAX_WIN, winSize(vm));
check('边界合法', vm.chunkStart >= 0 && vm.chunkEnd <= vm.messages.length,
      vm.chunkStart + '..' + vm.chunkEnd);
// 窗口有上限，滚到最早时尾部会被卸掉，这里只校验窗口不失控
check('滚到最早后窗口仍封顶', winSize(vm) <= consts.MAX_WIN, winSize(vm));

// ---------- 3. 锚定：窗口变化后同一条消息在视口中的位置不变 ----------
const vm2 = makeVm(30);
vm2.measure();
vm2.$refs.msgBox.scrollTop = vm2.padTop + 200;
const anchor = vm2.messages[vm2.chunkStart + 2].id;
const rectOf = (v, id) => {
  const el = v.$refs.msgBox.querySelector('.message[data-mid="' + id + '"]');
  return el ? el.getBoundingClientRect().top : null;
};
const before = rectOf(vm2, anchor);
check('锚点消息在视口内', before !== null && before >= 0 && before < VIEW, before);
vm2.onScroll();                                // scrollTop 已在顶部触发区 → 自动加载
const after = rectOf(vm2, anchor);
check('窗口加载后锚点仍在窗口内', after !== null, after);
check('锚定后视口内容不动（±2px）', after !== null && Math.abs(after - before) <= 2,
      before + ' -> ' + after);

// ---------- 4. 回到底部：尾部要能重新接回来 ----------
let guard2 = 0;
while (vm.chunkEnd < vm.messages.length && guard2++ < 60) {
  const b = vm.$refs.msgBox;
  b.scrollTop = b.scrollHeight - b.clientHeight;
  vm.onScroll();
}
check('回到底部时尾部接全', vm.chunkEnd === vm.messages.length, vm.chunkEnd);
check('接回尾部后仍受上限约束', winSize(vm) <= consts.MAX_WIN, winSize(vm));

// ---------- 5. 高度守恒：滚动条长度不该随窗口移动缩水 ----------
const vm3 = makeVm(30);
vm3.measure();
const h0 = totalH(vm3);
for (let k = 0; k < 12; k++) {
  vm3.$refs.msgBox.scrollTop = vm3.padTop + 10;
  vm3.onScroll();
  vm3.$refs.msgBox.scrollTop = 0;
  vm3.onScroll();
}
// 从未渲染过的条目只能用估算高度，误差随「测量覆盖率」收敛，允许 3 条以内
check('多轮窗口移动后总高度基本守恒（±3 条）', Math.abs(totalH(vm3) - h0) <= 3 * ITEM_H,
      totalH(vm3) + ' vs ' + h0);

// ---------- 6. 短会话 ----------
const vm4 = makeVm(1);                         // 2 条消息 + 欢迎语
check('短会话窗口覆盖全部', vm4.chunkStart === 0 && vm4.chunkEnd === vm4.messages.length,
      vm4.chunkStart + '..' + vm4.chunkEnd);
vm4.measure();
check('短会话无占位', vm4.padTop === 0 && vm4.padBottom === 0);
vm4.$refs.msgBox.scrollTop = 0;
vm4.onScroll();
check('短会话滚动不报错、窗口不变', winSize(vm4) === vm4.messages.length, winSize(vm4));

// ---------- 7. 流式期间不卸尾部 ----------
const vm5 = makeVm(30);
vm5.measure();
vm5.loading = true;
vm5.chunkStart = 13;
vm5.chunkEnd = 40;
vm5.$refs.msgBox.scrollTop = vm5.padTop + 10;
vm5.onScroll();
check('流式期间加载更早时不卸载尾部', vm5.chunkEnd === 40, vm5.chunkEnd);

console.log(fails ? '\n失败 ' + fails + ' 项' : '\n全部通过');
process.exit(fails ? 1 : 0);
