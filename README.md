# gen_rpt

一个可直接放在 GitHub repo 中运行的 **Deep Research 研究报告生成器**。

你可以在 GitHub Actions 里输入一段“选题描述”，然后自动完成这条链路：

1. 用 DeepSeek 生成研究计划
2. 自动做公开网页检索与资料抓取
3. 按 Deep Research + 管理咨询问题拆解方式组织内容
4. 自动生成品牌化洞察图卡与统一视觉风格图表
5. 自动生成封面页、目录、免责声明
6. 自动执行 PDF QA；若发现排版风险，会压缩内容并重新渲染
7. 同步输出 `HTML + Markdown + PDF + PPTX + HTML 演讲稿`
8. 把结果直接写回当前 repo 的 `reports/` 目录

## 现在已经支持什么

- **Action 输入一句话选题**，自动生成完整研究报告
- **BlueOcean 品牌化呈现**：主题色、字体、图表风格集中在 `branding/theme.json`
- **封面页**：自动生成正式封面；若你放入自定义封面图则优先使用自定义图
- **每页 PDF Logo**：当前使用 dummy 的 `BlueOcean` logo，可直接替换
- **目录页**：自动生成章节目录
- **免责声明**：自动生成“不构成投资建议”等正式声明
- **金字塔原理**：标题和导语要求结论先行、crisp & sharp
- **七步法与 issue tree**：先拆问题，再收集证据、定位要害、形成建议
- **战略十问思维**：融合市场竞胜力、优势来源、趋势、不确定性、执行决心等维度
- **Reference 弱化**：正式文件不直接列参考链接，只保留机构来源说明
- **Reference backup**：完整来源底稿自动进入 `backup/` 文件夹
- **PDF QA**：自动检查 PDF 文本重叠、字体过小、异常大字、元标签泄露、页面密度风险
- **自动修复**：QA 不通过时，会自动截短标题/正文/图表标签，切换 compact profile 并重渲染
- **多格式输出**：自动生成 `report.html`、`report.md`、`report.pdf`、`report.pptx`、`presentation.html`
- **写回 repo**：不是 artifact，而是直接 commit 到仓库
- **中文图表字体修复**：workflow 会安装 CJK 字体，并在 Matplotlib 中自动 fallback

## 项目结构

```text
gen_rpt/
├── .github/workflows/generate_deep_research.yml
├── branding/
│   ├── theme.json
│   └── logo.svg
├── gen_rpt/
│   ├── brand_assets.py
│   ├── deepseek_client.py
│   ├── graphics.py
│   ├── pdf_qa.py
│   ├── pdf_renderer.py
│   ├── ppt_renderer.py
│   ├── presentation_renderer.py
│   ├── report_renderer.py
│   ├── research_pipeline.py
│   ├── theme.py
│   └── web_fetch.py
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
  report.pptx
  presentation.html
  qa_result.json
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
    qa/
      pdf_qa.json
      page_001.png
      page_002.png
```

## PDF QA 与自动修复

生成流程现在是：

```text
render HTML/MD/PDF
→ run_pdf_qa(report.pdf)
→ 如果通过：继续生成 PPTX 与 HTML 演讲稿
→ 如果不通过：apply_pdf_qa_fixes()
→ 重新渲染 HTML/MD/PDF
→ 再跑一次 QA
→ 生成 PPTX 与 HTML 演讲稿
```

QA 会检查：

- PDF 是否能打开
- 页数是否异常
- 文本是否可抽取
- 是否存在文本块重叠
- 是否存在过小字体
- 是否存在异常大字
- 是否泄露 `BCG-style`、`McKinsey-style`、`sample card` 等内部元标签
- HTML 页面是否存在文字过密 + 图片同页的溢出风险

QA 截图会保存在：

```text
reports/.../backup/qa/
```

如果触发自动修复，原始模型输出会保留为：

```text
report_payload_prefixed.json
```

最终正式使用的是：

```text
report_payload.json
```

## PPTX 与 HTML 演讲稿

除了正式研究报告，系统还会生成：

```text
report.pptx
presentation.html
```

- `report.pptx`：适合继续手工编辑、发给团队修改
- `presentation.html`：适合直接浏览器演示，支持键盘翻页
  - 右箭头 / 空格：下一页
  - 左箭头：上一页
  - F：全屏

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

### 1. 替换 PDF / PPT Logo

当前 dummy logo 在这里：

```text
branding/logo.svg
```

你后面只要把这个文件替换成你们正式 logo 即可。

### 2. 替换封面底图

如果存在以下文件，系统会优先使用它：

```text
branding/cover_background.png
branding/cover_background.jpg
```

如果不存在，代码会自动生成一张抽象封面背景。

### 3. 替换主题色

主题配置文件在：

```text
branding/theme.json
```

这里控制：

- HTML / PDF 主题色
- 图卡 / 图表配色
- PPTX 配色
- 演讲稿 HTML 配色
- 品牌名
- 字体链路

## 正式文件与 backup 的区别

正式文件包括：

- `report.html`
- `report.md`
- `report.pdf`
- `report.pptx`
- `presentation.html`

正式文件里不会直接列出完整 Reference 链接清单，只会在文末用灰色小字说明参考了哪些机构或平台的公开研究。

完整来源底稿会进入：

```text
reports/.../backup/
```

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

```bash
export DEEPSEEK_API_KEY=你的key
python -m gen_rpt.main \
  --topic "生成一份关于 AI Agent 市场演进、竞争格局与商业化路径的深度研究报告" \
  --language zh \
  --target-length 3000 \
  --model deepseek-chat \
  --out-root reports
```

## 报告生成逻辑

- **Plan**：研究目标、决策问题、issue tree、检索词
- **Search**：公开网页检索与抓取
- **Read**：抽取网页正文，整理资料池
- **Synthesize**：结构化报告 JSON
- **Visualize**：生成 BlueOcean memo 风格图卡与图表
- **Render**：HTML、Markdown、PDF、PPTX、HTML 演讲稿
- **QA**：PDF 自动检测与自动修复
- **Archive**：来源底稿和 QA 截图进入 backup

## 当前版本的边界

- PDF QA 是启发式检测，能发现明显重叠/字体异常/密度风险，但不是完整视觉理解模型
- 自动修复主要通过压缩文本、截短标签、减少页面密度实现
- 当前默认封面背景是程序化生成，不是真正接入图像模型生成
- 公开网页搜索依赖通用搜索页面结构，后续可继续增强稳定性
- 图表数据主要来自模型对公开资料的结构化整理，复杂定量研究可继续叠加专门数据源

## 作者

- [@yt-feng](https://github.com/yt-feng)
