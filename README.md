# gen_rpt

一个可直接放在 GitHub repo 中运行的 **Deep Research 研究报告生成器**。

你可以在 GitHub Actions 里输入一段“选题描述”，然后自动完成这条链路：

1. 用 DeepSeek 生成研究计划
2. 自动做公开网页检索与资料抓取
3. 按 Deep Research 风格组织章节
4. 自动生成 **品牌化洞察图卡** 与统一视觉风格图表
5. 自动生成 **封面页 + 目录 + 免责声明**
6. 同步输出 `HTML + Markdown + PDF`
7. 把结果直接写回当前 repo 的 `reports/` 目录

## 现在已经支持什么

- **Action 输入一句话选题**，自动生成完整研究报告
- **品牌化呈现**：当前默认品牌名为 `BO Institute Strategy Agent`
- **封面页**：自动生成正式封面；若你放入自定义封面图则优先使用自定义图
- **每页 PDF Logo**：当前使用 dummy 的 `BlueOcean` logo，可直接替换
- **目录页**：自动生成章节目录
- **免责声明**：自动生成“不构成投资建议”等正式声明
- **标题更锋利**：已在提示词里强化金字塔原理、结论先行、crisp & sharp
- **Reference 弱化**：正式文件不再直接列参考链接，只保留灰色小字的机构来源说明
- **Reference backup**：完整来源底稿自动进入 `backup/` 文件夹
- **主题外置配置**：支持通过 `branding/theme.json` 统一调整主题色与品牌文案
- **语言切换**：支持 `zh / en`，且会真实影响提示词与输出文案
- **篇幅控制**：支持在 Action 里限定目标长度
  - 中文默认 `3000` 字
  - 英文默认 `1500` words
- **多格式输出**：自动生成 `report.html`、`report.md`、`report.pdf`
- **写回 repo**：不是 artifact，而是直接 commit 到仓库
- **中文图表字体修复**：workflow 会安装 CJK 字体，并在 Matplotlib 中自动 fallback
- **重叠优化**：已从模型输出长度约束 + 绘图布局动态缩放两个层面减少图卡和图表文字重叠

## 项目结构

```text
gen_rpt/
├── .github/workflows/generate_deep_research.yml
├── branding/
│   ├── theme.json
│   └── logo.svg
├── gen_rpt/
│   ├── __init__.py
│   ├── brand_assets.py
│   ├── deepseek_client.py
│   ├── web_fetch.py
│   ├── graphics.py
│   ├── report_renderer.py
│   ├── pdf_renderer.py
│   ├── research_pipeline.py
│   ├── theme.py
│   └── main.py
├── reports/
├── requirements.txt
├── .env.example
└── README.md
```

## 工作流说明

仓库里已经带了一个手动触发的 GitHub Actions：

- Workflow 名称：`Generate Deep Research Report`
- 触发方式：`Actions -> Generate Deep Research Report -> Run workflow`
- 输入参数：
  - `topic`：你的研究选题，可以直接输入一段话
  - `slug`：可选，自定义输出目录名
  - `language`：`zh` 或 `en`
  - `target_length`：可选，目标篇幅；留空时中文默认 3000、英文默认 1500
  - `model`：默认 `deepseek-chat`

运行完成后，生成结果会写入：

```text
reports/YYYY-MM-DD-your-topic-slug/
  report.html
  report.md
  report.pdf
  report_payload.json
  research_plan.json
  sources.json
  assets/
    brand-logo.svg
    cover-background.png
    card-1.png
    chart-1.png
  backup/
    reference_notes.md
    source_01.txt
    source_02.txt
```

## DeepSeek API Secret 配置

变量名已经预留好：

```bash
DEEPSEEK_API_KEY
```

请到仓库里这样配置：

1. 打开仓库
2. 进入 `Settings`
3. 进入 `Secrets and variables`
4. 点击 `Actions`
5. 新建 secret
6. Name 填：`DEEPSEEK_API_KEY`
7. Value 填你的 DeepSeek API Key

## 品牌与封面替换位置

### 1. 替换 PDF 每页 Logo

当前 dummy logo 在这里：

```text
branding/logo.svg
```

你后面只要把这个文件替换成你们正式 logo 即可。

### 2. 替换封面底图

当前逻辑是：

- 如果存在 `branding/cover_background.png` 或 `branding/cover_background.jpg`
  - 优先使用你放进去的图片
- 如果不存在
  - 代码会自动生成一张高质感抽象封面背景

也就是说，你之后想换成真正的 AI 封面图，只要把最终图片放到：

```text
branding/cover_background.png
```

就会覆盖当前自动生成的默认背景。

### 3. 替换主题色

主题配置文件在：

```text
branding/theme.json
```

你后面把附件里的 json 给我，或者你自己直接覆盖这个文件，都可以让以下内容同步变化：

- HTML 页面主题色
- 封面/图卡/图表配色
- 报告头部品牌名
- 强调色与系列色

## 正式文件与 backup 的区别

### 正式文件（给读者看）

正式文件包括：

- `report.html`
- `report.md`
- `report.pdf`

这些正式文件里：

- 有封面、目录、免责声明
- 有正文与图表
- **不会直接列出完整 Reference 链接清单**
- 只会在文末用灰色小字说明参考了哪些机构或平台的公开研究

### backup 文件夹（留档用）

完整来源底稿会进入：

```text
reports/.../backup/
```

里面包括：

- `reference_notes.md`
- `source_01.txt`
- `source_02.txt`
- ...

适合内部留档、追溯、二次核验。

## 本地运行

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 安装 PDF 依赖（本地）

本地如果也要生成 PDF，请额外安装：

```bash
wkhtmltopdf
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

然后把 `.env` 里的：

```bash
DEEPSEEK_API_KEY=your_deepseek_api_key_here
```

改成你自己的 key。

### 4. 执行生成

中文例子：

```bash
export DEEPSEEK_API_KEY=你的key
python -m gen_rpt.main \
  --topic "生成一份关于 AI Agent 市场演进、竞争格局与商业化路径的深度研究报告" \
  --language zh \
  --target-length 3000 \
  --model deepseek-chat \
  --out-root reports
```

英文例子：

```bash
export DEEPSEEK_API_KEY=your_key
python -m gen_rpt.main \
  --topic "Generate a deep research report on enterprise AI agents, market evolution, and monetization paths" \
  --language en \
  --target-length 1500 \
  --model deepseek-chat \
  --out-root reports
```

## 报告生成逻辑

当前实现参考了通用的 Deep Research Agent 工作流思路：

- **Plan**：先让模型拆研究目标、读者、章节和检索词
- **Search**：对公开网页做搜索与内容抓取
- **Read**：抽取网页正文，整理为资料池
- **Synthesize**：让模型输出结构化报告 JSON
- **Render**：自动渲染 HTML、Markdown、PDF、品牌化图卡和图表
- **Archive**：将来源底稿单独写入 backup 文件夹

## 当前版本的边界

当前版本已经能跑通从选题到报告落库的主链路，但也有这些边界：

- 当前默认封面背景是**程序化生成的高质感抽象背景**，并不是真正接入图像模型生成的 AI 图片
- 如果你后续要接真正的图像模型，可以保留当前渲染结构，只替换 `branding/cover_background.png`
- 公开网页搜索依赖通用搜索页面结构，后续可继续增强稳定性
- 图表数据主要来自模型对公开资料的结构化整理，复杂定量研究可继续叠加专门数据源
- 如果图卡文本极端偏长，虽然已经做了长度约束和动态缩放，仍建议进一步压缩源文案

## 作者

- [@yt-feng](https://github.com/yt-feng)
