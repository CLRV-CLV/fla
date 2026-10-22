# FLA · 画布翻页自动跳转与课件双向同步实现方案（备用技术文档）

> **文档性质**：技术储备与备用设计方案  
> **归档说明**：为保障当前版本的极简发版、100% 运行稳定与教师教学可控性，系统已暂时剥离画布翻页的自动跳转逻辑（回归纯手动翻页与手动对齐）。本文档完整记录已验证的课件与画布自动同步、微软 Office Online 通信握手、双 Frame 预加载、触屏手势识别及 AI 视觉识别等全部技术实现，供后续版本按计划平滑、分阶段重新上线时作为直接技术依据。

---

## 一、背景与问题分析

在面向大屏多媒体教学的场景中，底层通常内嵌了微软 Office 在线放映器（Microsoft Office Online Viewer），而表层则是 FLA 的透明板书墨迹画布（Ink Canvas）：
1. **课件步进 vs 画布换页冲突**：PowerPoint 幻灯片内往往包含多步“动画”（Animation Steps）。如果翻页笔（Clicker）或空格键一律触发画布换页，会导致第 1 页的第一步动画刚刚播放，画布就已经强行跳到了第 2 页，导致老师前一页写的板书消失。
2. **触屏书写 vs 触屏翻页误触**：教师在黑板或白板上画点、点击公式或短笔画书写时，触控时长短、位移小，容易被识别为“全屏轻触翻页（Tap to Turn）”，从而引起课件与墨迹的非预期跳转。
3. **跨域 iframe 隔离**：微软 Office 放映由跨域 iframe 提供服务，常规浏览器出于安全策略（SOP）无法直接读写 iframe 内的 DOM。因此，必须通过深链加载、postMessage 通信协议或视觉辅助技术实现精准协同。

---

## 二、方案一：微软 Office Online postMessage 握手与双向通信（最轻量推荐）

### 1. 通信激活条件
微软 Office 网页端默认不向外部派发广播事件。必须在嵌入的 URL 中携带专用参数：
```
&sftc=1
```
`sftc`（Support Frame To Client）会指示 Office 嵌入播放器在初始化时向宿主窗口（Parent Window）广播生命周期握手消息。

### 2. 双向握手协议时序
```
[微软 Office iframe]                      [FLA 宿主舞台]
        |                                       |
        | --- postMessage(App_IsFrameTrusted)-> |
        |                                       | (验证来源并确认父级受信任)
        | <-- postMessage(Host_IsFrameTrusted)- |
        | <-- postMessage(Host_PostmessageReady)|
        |                                       | (握手完成，进入全双工通信)
        | --- postMessage(Page/Slide Broadcast)| 接收页码变更
        | <-- postMessage(Action_NextSlide) --- | 下发翻页/步进指令
```

### 3. 完整实现代码备用

#### (1) 接收端监听与页码自动同步逻辑
```javascript
function onMsMessage(e) {
  if (S.dead || !e || !e.data) return;
  var msg = e.data;
  if (typeof msg === 'string') {
    try { msg = JSON.parse(msg); } catch (err) { }
  }
  if (!msg) return;

  // 1. 微软握手确认
  if (msg.MessageId === 'App_IsFrameTrusted') {
    var f = curFrame();
    if (f && f.contentWindow) {
      try {
        f.contentWindow.postMessage(JSON.stringify({
          MessageId: 'Host_IsFrameTrusted',
          SendTime: Date.now(),
          Values: { isTopFrameTrusted: true }
        }), '*');
        f.contentWindow.postMessage(JSON.stringify({
          MessageId: 'Host_PostmessageReady',
          SendTime: Date.now(),
          Values: {}
        }), '*');
      } catch (err2) { }
    }
    return;
  }

  // 2. 页面广播提取 (不同版本 Office 可能存放在根字段或 Values 字典中)
  var p = null;
  if (typeof msg.page === 'number') p = msg.page;
  else if (typeof msg.slide === 'number') p = msg.slide;
  else if (typeof msg.slideIndex === 'number') p = msg.slideIndex + 1;
  else if (msg.Values) {
    if (typeof msg.Values.page === 'number') p = msg.Values.page;
    else if (typeof msg.Values.slide === 'number') p = msg.Values.slide;
    else if (typeof msg.Values.slideIndex === 'number') p = msg.Values.slideIndex + 1;
  }

  // 3. 驱动画布自动跳转 (后续恢复时，可加入防抖消抖或显式跟随开关)
  if (p && p >= 1 && p <= total() && p !== S.page) {
    console.log('[MSStage] 收到微软页面变更广播，画布自动同步至第:', p, '页');
    goPage(p);
  }
}
window.addEventListener('message', onMsMessage, false);
```

#### (2) 主动下发控制指令与焦点穿透
向微软播放器下发统一动作集合：
```javascript
function postNavToIframe(f, dir) {
  f = f || curFrame();
  if (!f || !f.contentWindow) return;
  try {
    var isNext = dir === 'next';
    var msgs = [
      { MessageId: isNext ? 'Action_NextSlide' : 'Action_PreviousSlide', SendTime: Date.now(), Values: {} },
      { MessageId: isNext ? 'UI_Next' : 'UI_Prev', SendTime: Date.now(), Values: {} },
      { MessageId: 'Action_NavigateTo', SendTime: Date.now(), Values: { direction: isNext ? 'next' : 'previous' } },
      { MessageId: 'Grab_Focus', SendTime: Date.now(), Values: {} }
    ];
    msgs.forEach(function (m) {
      f.contentWindow.postMessage(JSON.stringify(m), '*');
    });
  } catch (err) {}
}

function focusIframe() {
  var f = curFrame();
  if (f && f.contentWindow) {
    try {
      f.contentWindow.focus();
      f.contentWindow.postMessage(JSON.stringify({ MessageId: 'Grab_Focus', SendTime: Date.now(), Values: {} }), '*');
    } catch (e) { }
  }
}
```

---

## 三、方案二：双 Frame 预加载与真实 `wdSlideId` 深链跳转

微软 embed.aspx 页面初次加载通常需要 1.5~2.5 秒，若每页都销毁 iframe 重新加载会导致明显白屏。

### 1. 服务端 PPTX 结构解析
在 `server/msview.py` 中解析 PPTX 压缩包的 `ppt/presentation.xml`：
```python
# 提取幻灯片真实 sldId 列表
import xml.etree.ElementTree as ET
import zipfile

with zipfile.ZipFile(pptx_path) as z:
    with z.open('ppt/presentation.xml') as f:
        root = ET.fromstring(f.read())
        # 命名空间通常为 http://schemas.openxmlformats.org/presentationml/2006/main
        sld_ids = [int(el.attrib['id']) for el in root.iter('{*}sldId')]
```
返回给前端的模板格式：
```
url_tpl = "https://view.officeapps.live.com/op/embed.aspx?src={SRC}&wdStartOn={n}&wdSlideId={id}&sftc=1"
```

### 2. 前端双 Frame 预加载与热切换（A/B Buffer）
在前端初始化两个 iframe（`frameA` 与 `frameB`），实现无缝交叉淡入淡出：
```javascript
// 当停留在当前页 S.page 时，提前在后台 iframe 预载 S.page + 1
function preloadNext() {
  var n = S.page + 1;
  if (n > S.slides || frameWith(n)) return;
  var idleF = getIdleFrame();
  if (idleF) {
    loadInto(idleF, n); // 后台加载完毕后待命
  }
}

// 换页时瞬间切换可见性，完全无白屏
function swapTo(f) {
  S.frames.forEach(function (it) {
    if (it === f) {
      it.style.visibility = 'visible';
      it.style.opacity = '1';
      it.style.zIndex = '1';
    } else {
      it.style.visibility = 'hidden';
      it.style.opacity = '0';
      it.style.zIndex = '0';
    }
  });
}
```

---

## 四、方案三：全屏触控翻页手势识别与防冲突算法

### 1. 误触痛点与解决策略
在墨迹书写时，若手指或电容笔点击画布，极易被判定为翻页点击。防误触核心准则：
1. **书写工具保护**：当工具处于画笔（`pen`）、荧光笔（`marker`）、图形（`shape`）或橡皮（`eraser`）时，**绝对禁止**触发翻页。
2. **模式限定**：全屏触屏翻页**仅允许在光标模式（`cursor`）下生效**。
3. **安全区域裁剪**：避开顶栏（顶部 70px）、底栏（底部 80px）与两侧工具箱（左右各 70px）。

### 2. 手势算法参考实现
```javascript
function handlePointerTap(e) {
  // 1. 严格检查：非光标模式一律不触发翻页
  if (S.tool !== 'cursor') return;

  var cx = e.clientX, cy = e.clientY;
  var isTopBar = cy < 70;
  var isBotBar = cy > window.innerHeight - 80;
  var isSideBar = cx < 70 || cx > window.innerWidth - 70;
  if (isTopBar || isBotBar || isSideBar) return;

  // 2. 区分左右半屏 (右侧翻至下一页，左侧翻至上一页)
  if (cx > window.innerWidth * 0.4) {
    triggerNext();
  } else {
    triggerPrev();
  }
}
```

---

## 五、方案四：AI 视觉识屏 / 图像比对兜底方案（OCR / pHash）

当第三方平台阻断了 postMessage 或参数失效时，可通过纯客户端视觉方案检测当前页码：
1. **浏览器捕获机制**：
   ```javascript
   const stream = await navigator.mediaDevices.getDisplayMedia({
     video: { displaySurface: "browser" },
     preferCurrentTab: true
   });
   ```
2. **局部区域特征采样**：
   - 提取右下角（页码常驻区域）的 Canvas 局部灰度图像。
   - 使用 Web Worker 中的 Tesseract.js 或服务端预生成的各页感知哈希（pHash）对比：
   ```javascript
   function hammingDistance(hash1, hash2) {
     let diff = 0;
     for (let i = 0; i < hash1.length; i++) {
       if (hash1[i] !== hash2[i]) diff++;
     }
     return diff;
   }
   ```
3. **为何当前版本剥离**：
   - 需要用户授予录屏权限，教师上课容易受到浏览器弹窗干扰。
   - 低性能设备（如 Android 教学一体机）运行后台图像分析会有卡顿。

---

## 六、后续版本分阶段恢复实施路线图 (Rollout Roadmap)

若后续需要重新开放课件与画布的自动同步，建议按以下三步分期上线：

| 阶段 | 目标与实现内容 | 验收标准 |
| :--- | :--- | :--- |
| **第一阶段（显式开关）** | 在放映设置面板中增加 `[x] 课件与画布跟随翻页 (实验性)` 开关，默认处于**关闭（手动模式）**状态。由需要的高级用户自选启用。 | 手动模式下保持现有纯净状态；勾选后才挂载 `message` 自动同步监听。 |
| **第二阶段（消抖与动画过滤）** | 针对 PowerPoint 幻灯片内含多步动画的情形，在 `onMsMessage` 中增加 300ms 延迟消抖（Debounce），并比对步进状态，避免单页内多动画播放时误切画布。 | 幻灯片多步动画播放期间画布保持不动，只有真实切页时才平滑换页。 |
| **第三阶段（多端一体机灰度）** | 收集大屏一体机触控笔、激光翻页笔（USB HID 设备）的键值映射兼容性报告，针对不同硬件做按键归一化。 | 翻页笔短按推进动画、长按换页，画布与课件完全对齐。 |

## 七、翻页按键与工具栏架构决策备忘 (2026-09-19)

### 1. 移除左侧工具栏翻页按键（`pgprev` / `pgnext`）
- **现象痛点**：若老师在 PPT 中通过翻页笔或屏幕点击放映至第 5 页，因未手动切换板书页，顶栏仍显示第 1 页。此时老师若随手点击左侧工具栏的“下一页”按键，代码会调用 `goPage(2)` 并触发微软 iframe 重新加载第 2 页，导致演示进度突然从第 5 页被强行拉回第 2 页。
- **决策结论**：左侧工具栏（`barL`）只保留绘图工具、撤销重做和“加一页板书（`addpage`）”，彻底移除 `pgprev` 和 `pgnext` 按键。画布翻页仅允许老师通过顶栏页码/缩略图抽屉显式手动切换。

### 2. 翻页按键（上下左右/PageUp/PageDown/空格）完全不拦截原则
- **现象痛点**：若前端脚本在 `window.onkeydown` 中拦截上下左右键或空格并调用 `e.preventDefault()`，会导致实体翻页笔（USB HID 设备）向浏览器发送的标准步进按键被脚本截断，微软 iframe 无法收到按键。
- **决策结论**：
  1. FLA 脚本对 `ArrowUp`, `ArrowDown`, `ArrowLeft`, `ArrowRight`, `PageUp`, `PageDown`, `Space` 等翻页笔常用按键**一律不做拦截、不调用 preventDefault、不改变画布页码**。
  2. 在任何工具（包括选择光标、画笔、荧光笔、橡皮等）操作完毕后（`setTool`, `endStroke`），自动将窗口焦点交还给微软 iframe（`curFrame().contentWindow.focus()`）。
  3. 在选择模式（光标工具）下，墨迹层 `pointer-events: none`，老师点击屏幕任何区域直接由微软 iframe 原生响应；在使用翻页笔时，按键由微软 iframe 原生响应，FLA 绝不私自处理或篡改幻灯片。

---
*归档日期：2026-09-19*  
*维护团队：FLA 核心开发组*
