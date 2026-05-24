# Track 2 上手指南 —— Cloud Run dispatcher 服务

> 阅读对象：A（接手 Phase 5f Track 2）
> 作者：C (Jiayu) —— 任何不清楚的地方在群里 at 我
> 状态：本文档 + `.gitignore` + `_stub_stream.json`（C 已 capture 的真实 supervisor 事件流）已就位。其余文件 A 从零建。

英文版见 [ONBOARDING.en.md](ONBOARDING.en.md)。

---

## 1. 这个服务在系统中的位置

```
Elastic Workflow (5min 定时触发)
    │  POST /dispatch  { "published_doi": "...", "preprint_doi": "..." }
    │  Authorization: Bearer <static token in WF YAML>
    ▼
[Cloud Run "dispatcher"]   ← 你的范围
    │ (a) bearer 鉴权
    │ (b) ES 读取：用两个 DOI 反查完整 preprint + published 文档
    │ (c) 用 Cloud Run 自己的 service-account 身份 mint GCP access token
    │ (d) 调用 Vertex AI Agent Engine 上的 supervisor_agent：
    │       POST .../reasoningEngines/<SUPERVISOR_ID>:streamQuery
    │     解析 stream → 收集
    │       1× drift_event、N× affected_citations、N× notification drafts
    │ (e) ES bulk-write：drift_events、affected_citations、notification_log (status=drafted)
    │ (f) 对每个 notification draft：Gmail API 发邮件 → 更新 notification_log status=sent
    ▼
HTTP 202 Accepted（fire-and-forget；整条 pipeline 可能跑几分钟）
```

虚线框内所有事都是你的。C 负责：
- 步骤 (d) 另一侧的 supervisor agent —— 以稳定 HTTP endpoint 形式暴露
- 步骤 (a) 另一侧的 Elastic Workflow YAML —— 发送固定 payload 格式

你和 C 之间的契约：**输入格式 (a) + 输出行为 (e)+(f)**。中间 (b)-(f) 你怎么实现都可以，只要 ES + Gmail 的副作用符合 schema 即可。

> **关于 supervisor 内部（仅供了解，你不需要做任何事）**：supervisor 是一个 custom ADK `BaseAgent`，内部已经实现了 §4.1 的 fan-out —— `claim_extractor` ×2 并行 → `drift_analyzer` → `citation_finder` → `notifier` ×N（每个 affected citation 调一次，并行）。你消费合并后的 event stream 作为一段扁平序列。你在步骤 (d) 的工作只是识别每个 event 来自哪个 sub-agent、把最终输出路由到对应处理。你**不需要**自己 per-citation 调 `notifier` —— supervisor 已经做了。

---

## 2. 必读材料（按顺序，约 30 分钟）

1. **`docs/contracts.md` §9.6.1** —— 编排拓扑 + dispatcher 存在的理由（不要跳，它解释了为什么不是直接从 Workflow YAML 调 supervisor）
2. **`docs/contracts.md` §2.2.3 / §2.2.4 / §2.2.6** —— 你 bulk-write 的三个 ES 索引 schema (`drift_events`、`affected_citations`、`notification_log`)
3. **`docs/contracts.md` §2.2.1** —— `preprints` 索引 schema（步骤 (b) 你要 SELECT 的字段在这里）
4. **`docs/contracts.md` §3.2.2 / §3.3.2 / §3.4.2** —— supervisor 内部各 sub-agent 的输出 schema —— 这些就是 stream 里出现的 JSON 形状、也是你要持久化进 ES 的格式
5. **`docs/contracts.md` §2.3** —— `record_source` 字段规则。**你写入的所有记录都不要设置这个字段** —— 留空即可（真实数据约定；demo seed 在别处加 tag）
6. **`agents/_DEPLOY_CHECKLIST.md`** —— 状态表里有 5 个 sub-agent 的 reasoning-engine ID；帮你建立部署模式的感觉（你不会部署 ADK agent，但表里的 ID 间接喂给 supervisor）

略读即可 —— 不用全文吸收。上面 6 段是承重墙。

---

## 3. 你需要 C 提供的输入

| 项 | 值 / 来源 | 状态 |
|---|---|---|
| `SUPERVISOR_REASONING_ENGINE_ID` | **`7816826734824652800`** | ✅ 已部署（resource 在 `tensile-topic-496519-i1` / `us-central1`）|
| `_stub_stream.json` | `apps/dispatcher/_stub_stream.json` | ✅ 已 capture（C 跑过真实 supervisor 一次的完整事件流；A 用这个做 USE_STUB_STREAM 开发）|
| `WF_BEARER_TOKEN`（静态串）| 你和 C 商定一个值后写进 Secret Manager | ⏳ 群里商定（建议你直接生成一个 random 32-char 串发给 C）|
| `KIBANA_URL` + `ELASTIC_API_KEY` | `agents/.env`（仓库里已有，gitignored）| ✅ 现有 |
| GCP project + region | `tensile-topic-496519-i1`、`us-central1` | ✅ 仓库公开信息 |
| 子 agent reasoning engine ID（仅供查阅）| 见 `agents/_DEPLOY_CHECKLIST.md` 状态表 | dispatcher 不需要直接调，supervisor 会代调，但 trace 解析时可能会看到 |

你拥有 GCP **Project Editor** 角色，所以：
- 可以直接 `gcloud run deploy`，不需要 C 先建 service account —— Cloud Run 会用默认的 Compute Engine SA，它已经有你需要的所有 role（`aiplatform.user`、`secretmanager.secretAccessor`）。如果你想要干净一些，可以自己 `gcloud iam service-accounts create dispatcher-sa`。
- 可以直接读写 Secret Manager（`WF_BEARER_TOKEN` 等 secret 你自己创建 value）
- 可以随意 deploy / redeploy Cloud Run，不需要跟 C 协调
- 可以自己跑 OAuth setup script（Step 0），自己决定 secret 命名

`_stub_stream.json` 已经就位 —— Step 1 起步后就可以 `USE_STUB_STREAM=1` 走完 Step 4-6 的开发。Step 8 端到端联调时再切到真实 supervisor。

---

## 4. 任务步骤

每一步可独立运行验证。

### Step 0 —— Gmail OAuth setup（约 30 分钟，**最先做**）

dispatcher 要用 Gmail API 发邮件。个人 Gmail 账号（`gregjones11235@gmail.com`，非 Workspace）必须走 OAuth2 user consent + refresh token 路径（domain-wide delegation 仅 Workspace 才支持，已确认）。

写一个一次性脚本 `apps/dispatcher/scripts/gmail_oauth_setup.py`：

1. 在 GCP Console 创建 OAuth client（[Console → APIs & Services → Credentials → Create Credentials → OAuth client ID → Desktop app](https://console.cloud.google.com/apis/credentials)）。下载 `client_secret.json`，**不要 commit**。
2. 启用 Gmail API（[Console → APIs & Services → Enable APIs → Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com)）。
3. 脚本用 `google-auth-oauthlib` 跑 `InstalledAppFlow.from_client_secrets_file(...).run_local_server(port=0)`，浏览器跳转 → 你用 `gregjones11235@gmail.com` 同意 `https://www.googleapis.com/auth/gmail.send` scope。
4. 拿到 `credentials.refresh_token` 后，把以下三个值写进 Secret Manager（自己起 secret 名，建议 `gmail-refresh-token` / `gmail-oauth-client-id` / `gmail-oauth-client-secret`）：

```python
from google.cloud import secretmanager
client = secretmanager.SecretManagerServiceClient()
for name, value in [
    ("gmail-refresh-token", creds.refresh_token),
    ("gmail-oauth-client-id", creds.client_id),
    ("gmail-oauth-client-secret", creds.client_secret),
]:
    # 先 create secret（如果不存在），再 add version
    parent = f"projects/{PROJECT_ID}"
    try:
        client.create_secret(parent=parent, secret_id=name,
                             secret={"replication": {"automatic": {}}})
    except Exception:
        pass  # 已存在
    client.add_secret_version(parent=f"{parent}/secrets/{name}",
                              payload={"data": value.encode()})
```

脚本跑完只要不出错就 OK，不需要写回任何文件。dispatcher 启动时从 Secret Manager 拉这三个值（见 Step 6）。

参考：https://developers.google.com/workspace/gmail/api/auth/web-server

### Step 1 —— FastAPI service scaffold（约 20 分钟）

```
apps/dispatcher/
  main.py              # FastAPI app，单一 POST /dispatch endpoint
  requirements.txt     # fastapi, uvicorn, google-cloud-aiplatform, elasticsearch, google-api-python-client, google-auth, google-auth-oauthlib, google-cloud-secret-manager
  Dockerfile           # python:3.12-slim base，复制 main.py + requirements，ENTRYPOINT uvicorn
  .env.example         # WF_BEARER_TOKEN, KIBANA_URL, ELASTIC_API_KEY, SUPERVISOR_REASONING_ENGINE_ID, GCP_PROJECT
  .gitignore           # .env, __pycache__, scripts/client_secret*.json
  scripts/             # 一次性脚本：gmail_oauth_setup.py
  README.md            # 一段话「这是什么」，链回本 onboarding doc
```

`main.py` 骨架（每个 body 慢慢写）：

```python
import os
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI()

class DispatchRequest(BaseModel):
    published_doi: str
    preprint_doi: str

@app.post("/dispatch", status_code=202)
async def dispatch(req: DispatchRequest, authorization: str = Header(...)):
    # (a) 校验 bearer
    if authorization != f"Bearer {os.environ['WF_BEARER_TOKEN']}":
        raise HTTPException(401)
    # (b) ES 读 preprint + published 完整文档
    # (c) mint GCP access token（Cloud Run service-account ADC 自动处理 —— vertexai SDK 自动用）
    # (d) 调 supervisor stream_query，收集 outputs
    # (e) ES bulk-write
    # (f) 对每个 notification draft：Gmail 发送 → ES update
    return {"status": "accepted"}
```

本地跑：`uvicorn main:app --reload --port 8080`。

### Step 2 —— ES 反查（约 30 分钟）

用 `elasticsearch-py` async client。两次 GET：

```python
# 步骤 (b)
preprint_doc = await es.get(index="preprints", id=req.preprint_doi)
published_doc = await es.get(index="preprints", id=req.published_doi)  # 同一个 index；published_doi 也是其中一行
```

从两条记录里取出 `abstract`、`conclusion`、`title`、`version`。这些是 claim_extractor 按 §3.1.1 需要的字段。

注意：preprint 和它的 published 版本是 `preprints` 索引里**两条独立的行**，分别用各自的 DOI 作为 `_id`。链接关系是 preprint 行上的 `published_doi` 字段指向 published 行的 `_id`（Jeremy 的 puller 填这个字段）。

### Step 3 —— 构造 supervisor 输入 envelope + 调 streamQuery（约 1h）

Supervisor 接收一个 envelope，类似：

```json
{
  "preprint": { "doi": "...", "version": "v3", "title": "...", "abstract": "...", "conclusion": "..." },
  "published": { "doi": "...", "version": "v1", "title": "...", "abstract": "...", "conclusion": "..." }
}
```

（C 写 supervisor 时会最终敲定这个具体 shape，在群里同步。上面是 C 当前的目标 shape。）

调用模式（你 service 里唯一新的 GCP SDK 用法）：

```python
import json
import vertexai
from vertexai import agent_engines

vertexai.init(project="tensile-topic-496519-i1", location="us-central1")

supervisor = agent_engines.get(os.environ["SUPERVISOR_REASONING_ENGINE_ID"])

# 重要：ADK AdkApp 的 stream_query 签名是 (message, user_id, session_id?, run_config?)
# message 必须是 str 或 ADK Content dict。我们把 envelope JSON 序列化成 str 传进去。
# supervisor 内部 (C 写) 会 json.loads 拆出 preprint / published。
envelope_json = json.dumps(envelope)

events = []
async for event in supervisor.async_stream_query(
    message=envelope_json,
    user_id=f"dispatcher::{req.preprint_doi}",  # 用 preprint_doi 当 user_id 便于在 trace 里追踪
):
    events.append(event)
```

`async_stream_query` 是 async generator，整条 stream 可能跑 30s–5min（supervisor 内部要串调 5 个 sub-agent）。因为 FastAPI handler 本身是 async，你可以选择：
- 用 `asyncio.create_task(run_pipeline(envelope))` fire-and-forget，handler 立刻返回 202
- 或者 `await` 跑完整条 pipeline 再返回（更简单但 HTTP 连接持续几分钟，Cloud Run 默认 timeout 是 5 min 内 OK，超过要 `--timeout=900`）

推荐 fire-and-forget：handler 立刻返回 202，pipeline 后台跑：

```python
import asyncio
asyncio.create_task(run_pipeline(envelope))
return {"status": "accepted"}
```

stream 中每个 `event` 是 ADK Event 的 JSON dump（即 `Event.model_dump()` 的格式）。事件结构主要有三种：

```jsonc
// (i) 子 agent LLM 在思考 / 输出文本：
{ "author": "claim_extractor", "content": { "parts": [{ "text": "..." }] } }

// (ii) 子 agent 调用了 MCP 工具（drift_analyzer / citation_finder / memory_synthesizer 会有）：
{ "author": "drift_analyzer", "content": { "parts": [{ "function_call": { "name": "search_drift_patterns", "args": {...} } }] } }

// (iii) MCP 工具返回：
{ "author": "drift_analyzer", "content": { "parts": [{ "function_response": { "name": "search_drift_patterns", "response": {...} } }] } }
```

每个事件都有 `author` 字段，标识来源 sub-agent（值就是 `claim_extractor` / `drift_analyzer` / `citation_finder` / `notifier` / `memory_synthesizer`）。**用 `event.author` 区分来源 agent**，不要靠 `function_response.name`——`function_response` 里的 name 是 MCP 工具名（`search_drift_patterns` 等），不是 sub-agent 名。

### 怎么从 stream 里捞出每个 sub-agent 的最终 §3.x.2 结构化输出

**两种 path**，取决于子 agent 有没有 MCP 工具：

| Sub-agent | 有 MCP 工具？ | 最终输出在哪 |
|---|---|---|
| `claim_extractor` | ❌ | 该 agent 的**最后一个事件**的 `content.parts[*].text` 里 —— 是一段 JSON 字符串，**通常被 ` ```json ... ``` ` markdown code fence 包裹**（Gemini 习惯，无视 INSTRUCTION 的「JSON only」要求）。需要先剥 fence 再 `json.loads`。|
| `notifier` | ❌ | 同上 |
| `drift_analyzer` | ✅ search_drift_patterns | 同上（agent 调完 tool 后还是用 text part 输出最终 §3.2.2）|
| `citation_finder` | ✅ openalex_citing_works | 同上 |
| `memory_synthesizer` | ✅ 三个 drift_patterns 工具 | 同上 |

所以**所有 5 个 sub-agent 都是同一套提取逻辑**：找该 agent 的最后一个 text-part 事件，剥 markdown fence，`json.loads`。`function_response` 事件里的 response 是 MCP 工具结果，不是你要的最终输出。

参考 helper（C 在 supervisor 里用同样思路，可以原样拷过来）：

```python
def strip_markdown_fence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()

def extract_final_output(events: list[dict], agent_name: str) -> dict | None:
    """从 events 里捞 agent_name 的最终 JSON 输出。"""
    agent_events = [e for e in events if e.get("author") == agent_name]
    for ev in reversed(agent_events):
        parts = (ev.get("content") or {}).get("parts") or []
        combined = "".join(p.get("text", "") for p in parts if "text" in p)
        if not combined.strip():
            continue
        stripped = strip_markdown_fence(combined)
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            continue
    return None
```

**多次调用同一个 sub-agent**（`claim_extractor` 跑两次：preprint + published）：两次的事件按时间先后是 preprint→published；上面这个 helper 只拿"最后一个"会丢掉 preprint。如果需要分开，按 `event.invocation_id` 或时间戳分组，或者直接在 helper 里把 events 切片后分别提取。对于 dispatcher 场景**两次 claim_extractor 的中间结果其实不需要持久化**（业务上只用 drift_analyzer 的输出），所以你可以忽略它们，只 extract 这 4 个的最终输出：

- 1× `drift_analyzer` → 写 `drift_events` 索引
- 1× `citation_finder` → 写 `affected_citations` 索引（按 `affected_citations[]` 数组展开成 N 行）
- N× `notifier` → 写 `notification_log` 索引 + 触发邮件发送
- 1× `memory_synthesizer` → 不写 ES（memory_synthesizer 自己已经通过 MCP 写过 `drift_patterns` 索引；这个事件供 demo / 日志 / observability 即可）

### Step 4 —— 解析 stream → 收集 1 个 drift_event、N 个 affected_citations、N 个 notification drafts（约 30 分钟）

用 Step 3 给的 `extract_final_output(events, agent_name)` helper：

```python
drift_event = extract_final_output(events, "drift_analyzer")  # 符合 §3.2.2
citation_result = extract_final_output(events, "citation_finder")  # 符合 §3.3.2
affected_citations = citation_result["affected_citations"]  # N 项

# notifier 跑 N 次 —— extract_final_output 只拿最后一次，需要按 invocation_id 分组
notifier_events = [e for e in events if e.get("author") == "notifier"]
# 按 invocation_id 把 events group 起来，每组分别 extract，得到 N 个 notification draft
notifications = []  # 长度 N，每项符合 §3.4.2
```

**对 N=0 情况**（demo 用的 HCQ 合成 DOI 在 OpenAlex 里找不到任何引用，所以 `affected_citations: []`，notifier 跳过）：notifications 是空 list，跳过 Step 6 邮件发送，但仍然要写 drift_event。`_stub_stream.json` 就是这种 N=0 的样本，你的代码必须支持这种 case。

### Step 5 —— ES bulk-write（约 45 分钟）

三类记录用 `elasticsearch.helpers.async_bulk`。Document ID：
- `drift_events`：`event_id`（UUID，drift_analyzer mint；supervisor 在 null 时补填）
- `affected_citations`：`{drift_event_id}::{citing_doi}` 按 §2.2.4
- `notification_log`：`affected_citation_id` 按 §2.2.6

**dispatcher 自己填的字段（不是 agent 输出来的）**——因为 §3.x 留 null（参见 contracts §3.2.2 `analyzed_at`、§3.4.2 `drafted_at`、§3.5.2 `synthesized_at` 的 note）：

| 字段 | 所在索引 | 值 |
|---|---|---|
| `analyzed_at` | drift_events | `datetime.now(UTC).isoformat()` |
| `detected_at` | drift_events | 同上 |
| `scored_at` | affected_citations | 同上 |
| `drafted_at` | notification_log | 同上 |
| `status` | notification_log | `"drafted"`（步骤 (f) 后会变成 `"sent"`）|
| `record_source` | 三个索引都 | **不要设** —— 按 §2.3 留字段不存在 |

### Step 6 —— Gmail 发送 + status 更新（约 45 分钟）

Step 0 已经把 refresh token + OAuth client id/secret 写进 Secret Manager。dispatcher 启动时拉出：

```python
from google.cloud import secretmanager
client = secretmanager.SecretManagerServiceClient()

def fetch(name):
    path = f"projects/{PROJECT}/secrets/{name}/versions/latest"
    return client.access_secret_version(name=path).payload.data.decode()

refresh_token = fetch("gmail-refresh-token")
oauth_client_id = fetch("gmail-oauth-client-id")
oauth_client_secret = fetch("gmail-oauth-client-secret")
```

用 `google-api-python-client` 的 Gmail v1 发：

```python
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import base64
from email.mime.text import MIMEText

creds = Credentials(
    token=None, refresh_token=refresh_token,
    token_uri="https://oauth2.googleapis.com/token",
    client_id=oauth_client_id,
    client_secret=oauth_client_secret,
)
service = build("gmail", "v1", credentials=creds, cache_discovery=False)

msg = MIMEText(notification["body"])
msg["to"] = notification["recipient"]["email"]
msg["subject"] = notification["subject"]
raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

resp = service.users().messages().send(userId="me", body={"raw": raw}).execute()
```

成功：`es.update(index="notification_log", id=affected_citation_id, doc={"status": "sent", "sent_at": now_iso})`。

失败（Gmail HTTP error）：`status: "failed"`、`error_message: <exception text>`。

**个人 Gmail 每日上限**：账号（gregjones11235@gmail.com）每 24h 500 封，邮箱侧强制 —— 不能调高。Demo 用足够（单次 E2E run 发 3-8 封）。如果测试时超额，会收到 HTTP 403 `Daily user limit exceeded`，等就行，不用改代码。

### Step 7 —— Cloud Run 部署（约 30 分钟）

从 `apps/dispatcher/`：

```bash
gcloud run deploy claimdrift-dispatcher \
  --source . \
  --region=us-central1 \
  --project=tensile-topic-496519-i1 \
  --allow-unauthenticated \
  --set-env-vars="GCP_PROJECT=tensile-topic-496519-i1,KIBANA_URL=...,SUPERVISOR_REASONING_ENGINE_ID=..." \
  --set-secrets="WF_BEARER_TOKEN=wf-bearer:latest,ELASTIC_API_KEY=elastic-api-key:latest"
```

你有 Editor role，所以 Cloud Run 默认用的 Compute Engine SA 已经有你需要的所有 role（`aiplatform.user`、`secretmanager.secretAccessor`）。如果想干净一些用专属 SA，自己建一个、加 `--service-account=<your-sa>@tensile-topic-496519-i1.iam.gserviceaccount.com` —— v0 不必。

**`--allow-unauthenticated`** 是有意为之：Elastic Workflow 的 `http.request` 步骤只带 bearer header 调你的 endpoint（没有 GCP IAM identity）。Cloud Run IAM (`--no-allow-unauthenticated`) 会直接挡住这个调用。代码里的 bearer 校验是唯一鉴权层 —— v0 OK。Defense-in-depth（让 Workflow 走 OIDC token + Cloud Run IAM）是 post-v0 polish。

### Step 8 —— 端到端 smoke test（和 C 协同，约 1h）

C 主导。你提供部署后的 dispatcher URL。C 用 demo 数据 seed 一对 preprint+published，手动 POST 到你的 `/dispatch` endpoint 带上 bearer。观察：
- Cloud Run logs：看 8 步依次走完
- ES：确认 `drift_events`、`affected_citations`、`notification_log` 有新行
- Gmail：确认测试收件箱收到 3-8 封邮件

---

## 5. Stub stream（已就位）

`apps/dispatcher/_stub_stream.json` 已经存在（被 `.gitignore`，不会进 git），内容是 **C 跑真实 supervisor 一次后捕获的完整事件序列**，envelope 是 HCQ demo（`elastic/demo_seed/preprints.json` 里的 v3 preprint + published 那一对）。

这份 capture 是 **N=0 affected_citations 场景**（demo DOI 在 OpenAlex 没引用 → notifier fan-out 跳过）。这正是你**必须**支持的 edge case。如果之后需要 N>0 的 capture 用真实数据再跑一次（B 的 puller 上线后），C 重新跑 `agents/supervisor_agent/scripts/capture_stream.py` 替换文件即可。

在 `main.py` 加 toggle：

```python
import json
from pathlib import Path

if os.environ.get("USE_STUB_STREAM"):
    events = json.loads(Path(__file__).parent.joinpath("_stub_stream.json").read_text())
else:
    events = []
    async for chunk in supervisor.async_stream_query(message=envelope_json, user_id=user_id):
        events.append(chunk)
```

事件总数预期在 50-150 之间（具体看 LLM 输出多少 token 切多少 chunk）；`_stub_stream.json` 当前约 97KB。

---

## 6. 你不需要操心的事

- **Supervisor agent 代码** —— C 写
- **Scheduled Elastic Workflow YAML** —— C 写。C 最后只需要你部署好的 dispatcher URL + 商定的 bearer token
- **Pullers 入库 `preprints`** —— B (Jeremy) 并行做 Cloud Run Job + Scheduler。在 B 的 puller 上线前，smoke test 用 `elastic/scripts/seed_demo_to_es.py` seed 的数据
- **§6.1 SSE adapter（给前端）** —— 不在 5f 范围。不要尝试 stream 事件给任何人 —— 只消费 supervisor stream + 写副作用

---

## 7. 完成标准 (Definition of Done)

- [ ] Step 0：OAuth setup 跑通，三个 Gmail secret 在 Secret Manager 里能 read 出来
- [ ] `apps/dispatcher/` 有 Step 1 列出的 6 个文件 + `scripts/gmail_oauth_setup.py`
- [ ] 本地 `uvicorn` 跑起来 + `curl -H "Authorization: Bearer ..." -d '{...}' localhost:8080/dispatch` 100ms 内返回 202
- [ ] 设 `USE_STUB_STREAM=1` 跑整条 pipeline 走 stub：ES 出现新行、Gmail 收到 fake 邮件
- [ ] `gcloud run deploy` 成功；service URL 返回
- [ ] C 给你真实的 `SUPERVISOR_REASONING_ENGINE_ID`；你 set 进环境变量、移除 `USE_STUB_STREAM`
- [ ] E2E smoke test (Step 8) 跑通

遇到 blocker 群里 at C。任何味道像「缺 C 端输入」的事，磨超过 30 分钟就停下来问。
