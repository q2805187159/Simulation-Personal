# 数字人生成与问答 POC 设计

## Intent

实现一个可以实际操作和验证的端到端 POC。用户从生成选项开始，系统通过 LLM 编排工具和受控 DSL 生成时间方向的 LifeGraph，暂停在骨架审阅断点。用户可以手动编辑骨架，或给 LLM 提建议，由系统创建子分支、重算依赖并校验。用户确认具体图修订后，LLM 分块生成血肉内容。独立审查发现问题后执行局部修复或返回骨架断点。通过后将图、血肉、索引状态和审查结果作为同一个档案发布。用户从档案库选择数字人、时点和版本，通过受限工具检索档案，以第一人称生成带证据边界的角色回答。

POC 使用确定性本地 LLM 适配器，不依赖外部模型密钥即可完成完整演示。后端保留 OpenAI-compatible 适配边界，后续只替换模型适配器和工具配置，不改工作流与数据库契约。

## Goals

- 让用户完整走通 `spec → skeleton_review → flesh → review → release → chat`。
- 让用户看到真实的 LifeGraph 节点、时间关系、共享事件和父子修订差异。
- 让手动编辑与 LLM 建议使用同一个图修订接口。
- 持久化 `GenerationSpec`、`graph_revision_id`、`detail_revision_id`、`release_id`、`snapshot_id` 和会话证据。
- 展示 LLM 工具调用、状态变化、审查结果和失败原因。
- 提供 PostgreSQL Docker 运行方式和本地开发命令。
- 继承现有设计图卷的纸张背景、深色导航、绿色/蓝色/红色状态语义和中文排版。

## Non-goals

- POC 不校准中国人口的真实概率，也不把演示样本作为全国代表性数据。
- POC 不接入真实敏感微观调查，不采集真实身份信息。
- POC 不让 LLM 生成或执行 OpenM++ C++ 源码。
- POC 不实现真正的跨 case 社会网络仿真。
- POC 不把聊天消息自动写回人生图。

## Approaches

### React + Vite + FastAPI + PostgreSQL

前端用 React、Vite、TypeScript 和原生 CSS。后端用 FastAPI、SQLAlchemy async、Pydantic 和 PostgreSQL。工作流和领域逻辑放在后端，浏览器只负责展示状态和发起命令。LLM 适配器默认为 deterministic mock，可按环境变量切换 OpenAI-compatible provider。

这条路径与后续生产架构的边界最接近，能在 POC 阶段验证版本账本、工具调用和会话范围。

### 直接增强静态 HTML

可以快速继承现有设计图卷，但长期状态、类型契约和多页面工作流会集中到一个页面，难以验证实际的前后端边界。因此保留现有 HTML 作为研究图卷，不作为 POC 运行时。

### Next.js 全栈

能力完整，但当前仓库没有 Node 应用边界。对 POC 来说会把框架初始化、服务端渲染和领域工作流混在一起，验证重点不够集中。

## User Experience

### Main Workspace

首屏是数字人生成工作台，采用三栏密度布局。

- 左栏显示人物护照、生成任务状态、当前版本和阶段进度。
- 中栏显示 LifeGraph 时间线和节点详情。点击节点显示时间精度、参与者、证据、状态增量和依赖。
- 右栏显示当前 LLM 工作台、用户建议输入、工具调用记录、校验问题和下一步操作。

页面第一屏提供地区、民族、截止日期、人口入口、生存状态、详细程度和随机种子。出生年份与家庭范围可指定，也可交给系统按种子采样。系统依据生成的人物条件与目录规则匹配历史背景候选。点击生成后显示实际采样结果、候选依据和工具轨迹，完成后进入骨架审阅。

### Skeleton Review

骨架审阅显示明确的 `graph_revision_id`。用户可编辑节点标题、时间区间和备注，也可输入自然语言建议。手动编辑和 LLM 建议都会显示 diff，创建子修订并刷新后继影响列表。只有结构校验通过的版本可以点击“继续填充”。

### Flesh and Review

血肉阶段按生命阶段和事件簇显示内容块。每个块显示 `detail_revision_id`、锚定事件和生成状态。审查页显示通过、警告和必须修复项。局部修复不会改骨架，结构修复会返回骨架审阅并要求重新确认。

### Archive Chat

档案库列出已发布人物、版本、生命状态和摘要。会话创建时固定人物、`release_id`、`graph_revision_id`、`snapshot_id`、查询时点和角色范围。回答区显示第一人称正文和可展开的证据引用。页面始终保留“合成角色模拟”标识。

## Backend Boundaries

```text
frontend
  -> API gateway
    -> workflow service
      -> graph service
      -> flesh service
      -> review service
      -> archive/chat service
      -> tool registry
        -> mock LLM adapter / OpenAI-compatible adapter
        -> deterministic skeleton adapter
        -> source and rule catalog
    -> PostgreSQL
```

FastAPI 路由保持薄，只做认证占位、输入解析、错误映射和响应。服务层负责工作流和领域规则。数据库是版本账本的权威源，前端状态是派生视图。

## Workflow Contract

```text
request_received
  -> spec_ready
  -> skeleton_running
  -> skeleton_review
  -> flesh_running
  -> chain_review
  -> repair_loop
  -> publish_pending
  -> ready
```

每个任务保存 `job_id`、`run_id`、状态、进度、幂等键、输入摘要、错误类型和实际产物。刷新或重复点击继续操作时返回现有作业，不创建重复任务。

## Core Data Model

- `generation_specs`: 用户选项、生成规格、来源和校验结果。
- `workflow_jobs`: 阶段状态、任务进度、错误和工具调用摘要。
- `graph_revisions`: 图版本、父版本、分支、发布状态和输入运行。
- `graph_nodes`: `event_id`、标题、时间区间、状态、证据状态、是否汇总节点。
- `graph_edges`: 时间边、拆分边、参与边、支持边和依赖边。
- `flesh_revisions`: `detail_revision_id`、依赖图版本、发布状态。
- `flesh_chunks`: 事件锚点、生命阶段、细节、记忆、叙事、证据引用和审查状态。
- `review_findings`: 审查类型、严重度、对象、问题、修复建议和状态。
- `releases`: 图、血肉、索引状态、验收状态和发布元数据。
- `persons`: 人物摘要、生命状态、地区、出生/死亡范围和合成标识。
- `chat_sessions`: 人物、发布、图修订、快照、时点、角色范围。
- `chat_turns`: 问题、检索工具、证据引用、回答、执行状态和模型配置。

## LLM Contract

POC 中的 LLM 角色分成五类。

1. 规格编排器。把用户选项编译成 `GenerationSpec`，只调用目录、人口框、参数和历史规则工具。
2. 骨架编辑器。提交受控图操作，不能写模型源码或绕过父版本。
3. 血肉生成器。按确认图版本生成细节块，不能隐式增加实质事件。
4. 独立审查器。从锁定的待审查版本读取，输出问题和证据，不签发自己生成的内容。
5. 人物问答器。固定发布和人物视角检索，只读调用证据工具，输出第一人称角色模拟。

默认 mock LLM 返回固定工具计划、固定骨架建议、固定细节和带证据引用的回答。环境变量 `LLM_PROVIDER=mock|openai_compatible` 控制适配器。实际模型名称、提示版本、温度和调用摘要写入 `workflow_jobs` 或 `chat_turns`。

## API Surface

- `POST /api/generations/spec/validate`
- `POST /api/generations`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/events`
- `GET /api/graphs/{graph_revision_id}`
- `POST /api/graphs/{graph_revision_id}/edits`
- `POST /api/graphs/{graph_revision_id}/continue-flesh`
- `GET /api/flesh/{detail_revision_id}`
- `POST /api/reviews/{release_candidate_id}/run`
- `POST /api/releases/{release_id}/publish`
- `GET /api/archive/persons`
- `POST /api/chat/sessions`
- `POST /api/chat/sessions/{session_id}/turns`
- `GET /api/chat/sessions/{session_id}`

所有写接口返回资源 ID、当前版本、状态和可恢复动作。长作业使用轮询，接口后续可替换为 SSE，不改变领域事件。

## Docker Runtime

`docker-compose.yml` 启动 PostgreSQL 和 FastAPI。前端本地用 Vite 启动并通过 `VITE_API_BASE_URL` 连接后端。默认数据库使用开发账号和本地卷。`/api/health` 返回数据库连接、迁移状态和 mock LLM 状态。

## Acceptance

- 用户可以从选项走到 `skeleton_review`，看到真实图节点和工具轨迹。
- 手动编辑和 LLM 建议都创建子版本，并显示父子差异。
- 用户点击继续后，填充任务固定当前图版本，重复点击不创建重复任务。
- 血肉块显示事件锚点和生成状态。
- 独立审查可以产生问题，结构问题回到骨架，局部问题只重写内容块。
- 发布后档案库只显示同一 release 的图、血肉和精确检索结果。
- 档案问答固定人物、版本和时点，工具调用和证据引用可见，回答不会修改人生图。
- 1440px、390px 和 320px 视口下无页面级横向溢出。
- Docker 启动、健康检查、API smoke test 和前端构建通过。
