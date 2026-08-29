"""
LLM Router - Using OpenRouter with Prompt Templates
LLM路由，使用OpenRouter和外部Prompt模板
"""
from fastapi import APIRouter, HTTPException, Query
from typing import List, Dict, Optional, Any
from pydantic import BaseModel
from datetime import datetime
import os
import json
import httpx
from pathlib import Path

from database import get_core_db, get_project_db
from routers.auth import verify_token

router = APIRouter()

# OpenRouter配置
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))
# Ordinary chat model for coding suggestions (not a specialized free/unstable model)
ANNOTATION_MODEL = os.getenv("ANNOTATION_MODEL", "openai/gpt-4o-mini")

# Report LLM provider: "openrouter" (default) | "illinois_chat"
LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or "openrouter").strip().lower()
ILLINOIS_CHAT_URL = os.getenv(
    "ILLINOIS_CHAT_URL",
    "https://chat.illinois.edu/api/chat-api/chat",
).strip()
ILLINOIS_CHAT_API_KEY = (os.getenv("ILLINOIS_CHAT_API_KEY") or "").strip()
ILLINOIS_CHAT_COURSE = (os.getenv("ILLINOIS_CHAT_COURSE") or "opencoder").strip()
ILLINOIS_CHAT_MODEL = (os.getenv("ILLINOIS_CHAT_MODEL") or "gpt-4o-mini").strip()

# Prompt模板目录
PROMPT_DIR = Path(__file__).parent.parent / "prompts"
PROMPT_DIR.mkdir(exist_ok=True)


# ============== Request/Response Models ==============

class WeeklyReportRequest(BaseModel):
    """周报请求"""
    project_id: str
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD
    model: Optional[str] = None


class MonthlyReportRequest(BaseModel):
    """月报请求"""
    project_id: str
    year: int
    month: int  # 1-12
    model: Optional[str] = None


class AnnotationRequest(BaseModel):
    """标注请求 — selected content + full codebook (+ optional project context)."""
    sentence: str  # selected / task content to code
    tag_groups: List[Dict]  # groups with options + descriptions
    project_id: Optional[str] = None
    project_memo: Optional[str] = None
    model: Optional[str] = None


class SuggestCodebookRequest(BaseModel):
    """LLM codebook suggestion — returns TagGroup-shaped JSON for the Code Book UI."""
    project_id: str
    user_prompt: Optional[str] = None
    sample_size: int = 100
    # replace = suggest a full codebook from samples; extend = add complementary groups
    mode: str = "replace"
    model: Optional[str] = None


# ============== Helper Functions ==============

def load_prompt_template(template_name: str) -> str:
    """
    从txt文件加载Prompt模板
    
    Args:
        template_name: 模板名称（如 "weekly_report", "monthly_report", "annotation"）
    
    Returns:
        Prompt模板内容
    """
    template_path = PROMPT_DIR / f"{template_name}.txt"
    
    if not template_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Prompt template not found: {template_name}.txt"
        )
    
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read prompt template: {str(e)}"
        )


async def call_openrouter(
    messages: List[Dict[str, str]],
    model: str = DEFAULT_MODEL,
    temperature: float = LLM_TEMPERATURE,
    max_tokens: int = LLM_MAX_TOKENS,
    force_json: bool = True,
    api_key: Optional[str] = None,
) -> Dict:
    """调用OpenRouter API。api_key 可覆盖环境变量（项目 Settings）。"""
    key = (api_key or OPENROUTER_API_KEY or "").strip()
    if not key:
        raise HTTPException(
            status_code=500,
            detail="OPENROUTER_API_KEY not configured (set in server .env or project Settings)"
        )
    
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("APP_URL", "http://localhost:8000"),
        "X-Title": "OpenCoder"
    }
    
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if force_json:
        payload["response_format"] = {"type": "json_object"}
    
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=payload
            )
            
            # Some models reject response_format — retry once without it
            if response.status_code != 200 and force_json:
                err_text = response.text or ""
                if "response_format" in err_text.lower() or response.status_code in (400, 422):
                    payload.pop("response_format", None)
                    response = await client.post(
                        f"{OPENROUTER_BASE_URL}/chat/completions",
                        headers=headers,
                        json=payload
                    )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"OpenRouter API error: {response.text}"
                )
            
            return response.json()
            
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="LLM request timeout")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM call failed: {str(e)}")


def _extract_illinois_text(payload: Any) -> str:
    """Best-effort extract assistant text from Illinois Chat JSON / stream chunks."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("message"), str):
            return payload["message"]
        msg = payload.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), str):
            return msg["content"]
        if isinstance(payload.get("content"), str):
            return payload["content"]
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            ch0 = choices[0] or {}
            if isinstance(ch0.get("delta"), dict) and ch0["delta"].get("content"):
                return str(ch0["delta"]["content"])
            if isinstance(ch0.get("message"), dict) and ch0["message"].get("content"):
                return str(ch0["message"]["content"])
            if isinstance(ch0.get("text"), str):
                return ch0["text"]
        if isinstance(payload.get("text"), str):
            return payload["text"]
        if isinstance(payload.get("error"), str):
            raise HTTPException(status_code=502, detail=f"Illinois Chat error: {payload['error']}")
        if isinstance(payload.get("message"), str) and payload.get("error"):
            raise HTTPException(status_code=502, detail=str(payload.get("message")))
    return ""


async def call_illinois_chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = LLM_TEMPERATURE,
    force_json: bool = False,
) -> Dict:
    """
    Illinois Chat / uiuc.chat course chatbot API.
    Uses env: ILLINOIS_CHAT_API_KEY, ILLINOIS_CHAT_COURSE, ILLINOIS_CHAT_MODEL, ILLINOIS_CHAT_URL.
    Prefers non-streaming; falls back to aggregating a stream.
    Returns OpenAI-shaped {choices:[{message:{content}}]} for callers.
    """
    key = ILLINOIS_CHAT_API_KEY
    if not key:
        raise HTTPException(
            status_code=500,
            detail="ILLINOIS_CHAT_API_KEY not configured (set in server .env)",
        )
    course = ILLINOIS_CHAT_COURSE
    if not course:
        raise HTTPException(
            status_code=500,
            detail="ILLINOIS_CHAT_COURSE not configured (set in server .env)",
        )

    use_model = (model or ILLINOIS_CHAT_MODEL or "gpt-4o-mini").strip()
    # Strip OpenRouter-style prefixes if someone passes openai/gpt-4o-mini
    if use_model and "/" in use_model and not use_model.startswith("llama"):
        parts = use_model.split("/", 1)
        if parts[0] in ("openai", "anthropic", "google", "meta-llama"):
            use_model = parts[1]

    msgs = list(messages)
    if force_json:
        # Illinois Chat has no response_format; reinforce JSON in system prompt
        reinforced = (
            "Respond with valid JSON only. Do not wrap in markdown fences. "
            "Do not add commentary outside the JSON object."
        )
        if msgs and msgs[0].get("role") == "system":
            msgs = [
                {**msgs[0], "content": f"{msgs[0].get('content', '')}\n\n{reinforced}"},
                *msgs[1:],
            ]
        else:
            msgs = [{"role": "system", "content": reinforced}, *msgs]

    # Illinois Chat API requires `model` on every request (course UI default is not applied).
    body: Dict[str, Any] = {
        "model": use_model,
        "messages": msgs,
        "api_key": key,
        "course_name": course,
        "stream": False,
        "temperature": temperature,
        "retrieval_only": False,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            # 1) Non-streaming
            res = await client.post(ILLINOIS_CHAT_URL, headers={"Content-Type": "application/json"}, json=body)
            text = ""
            if res.status_code == 200:
                try:
                    data = res.json()
                    text = _extract_illinois_text(data).strip()
                except Exception:
                    text = (res.text or "").strip()
            else:
                # 2) Streaming fallback
                body["stream"] = True
                async with client.stream(
                    "POST",
                    ILLINOIS_CHAT_URL,
                    headers={"Content-Type": "application/json"},
                    json=body,
                ) as stream_res:
                    if stream_res.status_code != 200:
                        err = await stream_res.aread()
                        raise HTTPException(
                            status_code=stream_res.status_code,
                            detail=f"Illinois Chat API error: {err.decode(errors='replace')[:500]}",
                        )
                    parts: List[str] = []
                    async for line in stream_res.aiter_lines():
                        if not line:
                            continue
                        raw = line[6:].strip() if line.startswith("data:") else line.strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(raw)
                            piece = _extract_illinois_text(chunk)
                            if piece:
                                parts.append(piece)
                        except json.JSONDecodeError:
                            parts.append(raw)
                    text = "".join(parts).strip()
                    if not text and res.status_code != 200:
                        raise HTTPException(
                            status_code=res.status_code,
                            detail=f"Illinois Chat API error: {(res.text or '')[:500]}",
                        )

            if not text:
                raise HTTPException(status_code=502, detail="Illinois Chat returned empty content")

            return {
                "choices": [{"message": {"content": text}}],
                "model": use_model,
                "provider": "illinois_chat",
            }
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Illinois Chat request timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Illinois Chat call failed: {str(e)}")


async def call_llm(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = LLM_TEMPERATURE,
    max_tokens: int = LLM_MAX_TOKENS,
    force_json: bool = True,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
) -> Dict:
    """
    Unified LLM call for reports.
    provider: illinois_chat | openrouter (default from LLM_PROVIDER env).
    """
    prov = (provider or LLM_PROVIDER or "openrouter").strip().lower()
    if prov in ("illinois_chat", "illinois", "uiuc_chat", "uiuc"):
        return await call_illinois_chat(
            messages=messages,
            model=model or ILLINOIS_CHAT_MODEL,
            temperature=temperature,
            force_json=force_json,
        )
    return await call_openrouter(
        messages=messages,
        model=model or DEFAULT_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        force_json=force_json,
        api_key=api_key,
    )


def _parse_llm_json(content: str) -> Dict:
    """Parse model JSON, tolerating markdown fences."""
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # drop first fence and optional last fence
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    return json.loads(text)


def _format_tag_groups_for_prompt(tag_groups: List[Dict]) -> str:
    """Rich codebook block: group desc + each tag value/label/description."""
    blocks = []
    for group in tag_groups:
        gid = group.get("group_id") or group.get("id") or ""
        gname = group.get("group_name") or group.get("name") or gid
        gtype = group.get("type") or "single"
        gdesc = (group.get("description") or group.get("group_description") or "").strip()
        lines = [f"### Group `{gid}` — {gname} ({gtype})"]
        if gdesc:
            lines.append(f"Group description: {gdesc}")
        lines.append("Options:")
        for opt in group.get("options") or []:
            val = opt.get("value") or opt.get("option_id") or opt.get("label") or ""
            lab = opt.get("label") or val
            odesc = (opt.get("description") or "").strip()
            if odesc:
                lines.append(f"  - value=`{val}` | label={lab} | description: {odesc}")
            else:
                lines.append(f"  - value=`{val}` | label={lab}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "(no tag groups)"


def _slug_id(text: str, fallback: str = "item") -> str:
    import re

    s = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    s = s[:48] or fallback
    return s


def _task_text_for_codebook(task: Dict, max_chars: int = 700) -> str:
    """Extract a short text sample from a task for codebook induction."""
    payload = task.get("payload") or {}
    title = (task.get("title") or "").strip()
    parts: List[str] = []
    if title:
        parts.append(title)

    text = (payload.get("text") or "").strip()
    if text:
        parts.append(text)

    meta = payload.get("meta") or payload.get("metadata") or {}
    bib = meta.get("bibliographic") if isinstance(meta, dict) else None
    if isinstance(bib, dict):
        for key in ("title", "authors", "abstract", "year"):
            v = bib.get(key)
            if v:
                parts.append(f"{key}: {v}")

    # Media filenames as weak signal when no transcript text
    for media_key in ("image", "video", "audio", "pdf"):
        media = payload.get(media_key)
        if isinstance(media, dict):
            fn = (media.get("original_filename") or "").strip()
            if fn:
                parts.append(f"{media_key}: {fn}")

    blob = " | ".join(p for p in parts if p).strip()
    if not blob:
        blob = f"(empty {task.get('task_type') or 'task'})"
    if len(blob) > max_chars:
        blob = blob[: max_chars - 1] + "…"
    return blob


def _normalize_suggested_tag_groups(raw: Any) -> List[Dict[str, Any]]:
    """
    Force LLM output into the TagGroupCreate shape the Code Book frontend expects.
    Accepts {"tag_groups": [...]} or a bare list.
    """
    if isinstance(raw, dict):
        groups_in = raw.get("tag_groups") or raw.get("groups") or raw.get("codebook") or []
    elif isinstance(raw, list):
        groups_in = raw
    else:
        groups_in = []

    if not isinstance(groups_in, list):
        raise ValueError("LLM response missing tag_groups array")

    out: List[Dict[str, Any]] = []
    seen_gids: set = set()

    for i, g in enumerate(groups_in):
        if not isinstance(g, dict):
            continue
        name = (g.get("name") or g.get("group_name") or f"Group {i + 1}").strip()
        gid = (g.get("group_id") or g.get("id") or "").strip() or _slug_id(name, f"group_{i + 1}")
        gid = _slug_id(gid, f"group_{i + 1}")
        base = gid
        n = 2
        while gid in seen_gids:
            gid = f"{base}_{n}"
            n += 1
        seen_gids.add(gid)

        gtype = (g.get("type") or "single").strip().lower()
        if gtype not in ("single", "multi"):
            gtype = "single"

        options_in = g.get("options") or g.get("tags") or g.get("labels") or []
        options_out: List[Dict[str, Any]] = []
        seen_oids: set = set()
        if isinstance(options_in, list):
            for j, opt in enumerate(options_in):
                if isinstance(opt, str):
                    label = opt.strip()
                    oid = _slug_id(label, f"opt_{j + 1}")
                    desc = ""
                elif isinstance(opt, dict):
                    label = (
                        opt.get("label")
                        or opt.get("name")
                        or opt.get("value")
                        or f"Option {j + 1}"
                    )
                    label = str(label).strip()
                    oid = (
                        opt.get("option_id")
                        or opt.get("value")
                        or opt.get("id")
                        or _slug_id(label, f"opt_{j + 1}")
                    )
                    oid = _slug_id(str(oid), f"opt_{j + 1}")
                    desc = (opt.get("description") or "").strip()
                else:
                    continue
                base_o = oid
                k = 2
                while oid in seen_oids:
                    oid = f"{base_o}_{k}"
                    k += 1
                seen_oids.add(oid)
                options_out.append(
                    {
                        "option_id": oid,
                        "label": label or oid,
                        "order": j + 1,
                        "active": True if not isinstance(opt, dict) else bool(opt.get("active", True)),
                        "description": desc,
                    }
                )

        if not options_out:
            continue

        out.append(
            {
                "group_id": gid,
                "name": name,
                "description": (g.get("description") or "").strip(),
                "type": gtype,
                "required": bool(g.get("required", False)),
                "order": int(g.get("order") or (i + 1)),
                "active": bool(g.get("active", True)),
                "options": options_out,
            }
        )

    if not out:
        raise ValueError("LLM returned no usable tag_groups")
    return out


async def fetch_project_data(project_id: str, start_date: str, end_date: str) -> Dict:
    """
    从其他端口获取项目数据
    
    这个函数调用您现有的API端点来获取数据
    """
    core_db = get_core_db()
    project_db = await get_project_db(project_id)
    
    if project_db is None:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 解析日期
    from datetime import datetime as dt
    start_dt = dt.strptime(start_date, "%Y-%m-%d")
    end_dt = dt.strptime(end_date, "%Y-%m-%d")
    # 将 end_dt 设置为当天的23:59:59，以包含整天的数据
    from datetime import timedelta
    end_dt = end_dt + timedelta(days=1, seconds=-1)
    
    # 获取数据 - 使用 completed_at 而不是 created_at（标注完成时间更准确）
    # 同时兼容两个字段
    annotations = await project_db.annotations.find({
        "$or": [
            {"completed_at": {"$gte": start_dt, "$lte": end_dt}},
            {"created_at": {"$gte": start_dt, "$lte": end_dt}}
        ]
    }).to_list(None)
    
    tasks = await project_db.tasks.find({
        "created_at": {"$gte": start_dt, "$lte": end_dt}
    }).to_list(None)
    
    print(f"📊 [Fetch Data] Date range: {start_date} to {end_date}")
    print(f"   Found {len(annotations)} annotations, {len(tasks)} tasks")
    
    # 获取项目信息
    from bson import ObjectId
    project = await core_db.projects.find_one({"_id": ObjectId(project_id)})
    
    # 获取团队成员
    coder_ids = list(set(str(a.get("coder_user_id")) for a in annotations if a.get("coder_user_id")))
    team_members = []
    for coder_id in coder_ids:
        try:
            coder = await core_db.users.find_one({"_id": ObjectId(coder_id)})
            if coder:
                team_members.append({
                    "name": coder.get("name", "Unknown"),
                    "annotations_count": len([a for a in annotations if str(a.get("coder_user_id")) == coder_id])
                })
        except:
            pass
    
    return {
        "project_name": project.get("name", "Project") if project else "Project",
        "period": f"{start_date} to {end_date}",
        "total_annotations": len(annotations),
        "total_tasks": len(tasks),
        "completed_tasks": len([t for t in tasks if t.get("status") == "completed"]),
        "team_members": team_members,
        "annotations_by_date": _group_by_date(annotations)
    }


def _group_by_date(annotations: List[Dict]) -> Dict[str, int]:
    """按日期分组统计"""
    result = {}
    for annotation in annotations:
        date = annotation.get("created_at", datetime.utcnow()).strftime("%Y-%m-%d")
        result[date] = result.get(date, 0) + 1
    return result


# ============== API Endpoints ==============

@router.post("/api/llm/weekly-report")
async def generate_weekly_report(
    request: WeeklyReportRequest,
    token: str = Query(...)
):
    """
    生成周报
    
    请求示例：
    ```json
    {
        "project_id": "65abc123...",
        "start_date": "2024-01-15",
        "end_date": "2024-01-21",
        "model": "anthropic/claude-3.5-haiku"
    }
    ```
    
    响应格式（固定）：
    ```json
    {
        "success": true,
        "report": {
            "title": "周报标题",
            "period": "2024-01-15 至 2024-01-21",
            "summary": "执行摘要",
            "sections": {
                "progress": {
                    "total_annotations": 250,
                    "completion_rate": 95.5,
                    "description": "进度描述"
                },
                "team_performance": {
                    "members": [...],
                    "top_performer": "张三",
                    "description": "团队表现描述"
                },
                "highlights": ["亮点1", "亮点2"],
                "issues": ["问题1", "问题2"],
                "recommendations": ["建议1", "建议2"]
            },
            "conclusion": "总结"
        },
        "metadata": {
            "generated_at": "2024-01-22T10:00:00Z",
            "model_used": "anthropic/claude-3.5-haiku",
            "cost": 0.002
        }
    }
    ```
    """
    # 验证用户权限
    try:
        print(f"🔍 [Weekly Report] Received token: {token[:50]}..." if len(token) > 50 else f"🔍 [Weekly Report] Received token: {token}")
        user = verify_token(token)  # verify_token不是async函数
        print(f"✅ [Weekly Report] Token verified, user: {user}")
    except Exception as e:
        print(f"❌ [Weekly Report] Token verification failed: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # 1. 从其他端口获取数据
    try:
        print(f"🔍 [Weekly Report] Fetching project data for {request.project_id}")
        project_data = await fetch_project_data(
            request.project_id,
            request.start_date,
            request.end_date
        )
        print(f"✅ [Weekly Report] Project data fetched successfully")
    except Exception as e:
        print(f"❌ [Weekly Report] Failed to fetch project data: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch project data: {str(e)}"
        )
    
    # 2. 加载Prompt模板
    try:
        prompt_template = load_prompt_template("weekly_report")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load prompt template: {str(e)}"
        )
    
    # 3. 填充Prompt
    prompt = prompt_template.format(
        project_name=project_data["project_name"],
        period=project_data["period"],
        total_annotations=project_data["total_annotations"],
        total_tasks=project_data["total_tasks"],
        completed_tasks=project_data["completed_tasks"],
        team_members="\n".join([f"- {m['name']}: {m['annotations_count']} annotations" for m in project_data["team_members"]]),
        annotations_by_date="\n".join([f"- {date}: {count} annotations" for date, count in project_data["annotations_by_date"].items()])
    )
    
    # 4. 调用LLM（OpenRouter 或 Illinois Chat，由 LLM_PROVIDER 决定）
    if LLM_PROVIDER in ("illinois_chat", "illinois", "uiuc_chat", "uiuc"):
        # API requires model on every call (UI chatbot selection is not inherited)
        model = request.model or ILLINOIS_CHAT_MODEL
    else:
        model = request.model or DEFAULT_MODEL
    messages = [
        {
            "role": "system",
            "content": "You are a professional project management report writer. Always return valid JSON format."
        },
        {
            "role": "user",
            "content": prompt
        }
    ]
    
    try:
        response = await call_llm(
            messages=messages,
            model=model,
            temperature=0.5,
            max_tokens=3000,
            force_json=True,
        )
        
        content = response["choices"][0]["message"]["content"]
        report = _parse_llm_json(content)
        
        # 5. 返回固定格式
        return {
            "success": True,
            "report": report,
            "metadata": {
                "generated_at": datetime.utcnow().isoformat(),
                "model_used": model,
                "provider": response.get("provider") or LLM_PROVIDER,
                "cost": 0.002  # 简化版，实际应计算
            }
        }
        
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse LLM response as JSON")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate report: {str(e)}")


@router.post("/api/llm/weekly-summary")
async def generate_weekly_summary(
    project_id: str = Query(...),
    token: str = Query(...)
):
    """
    生成极简周报 - 只返回几段话的总结
    
    响应格式：
    ```json
    {
        "success": true,
        "summary": "本周团队完成了250个标注任务...",
        "generated_at": "2024-01-22T10:00:00Z"
    }
    ```
    """
    # 验证用户权限
    try:
        user = verify_token(token)
        print(f"✅ [Weekly Summary] User verified: {user.get('email')}")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # 获取最近7天的数据
    try:
        from datetime import datetime, timedelta
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=7)
        
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")
        
        print(f"📊 [Weekly Summary] Fetching data from {start_str} to {end_str}")
        
        project_data = await fetch_project_data(
            project_id,
            start_str,
            end_str
        )
        
        # 提取关键数据
        total_annotations = len(project_data.get("annotations", []))
        total_tasks = project_data.get("total_tasks", 0)
        completed_tasks = project_data.get("completed_tasks", 0)
        completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
        
        # 团队成员数据
        team_members = project_data.get("team_members", [])
        active_coders = len([m for m in team_members if m.get("tasks_completed", 0) > 0])
        
        # 获取最近的 annotations 详细数据（用于生成更详细的报告）
        annotations_data = project_data.get("annotations", [])
        annotations_by_date = project_data.get("annotations_by_date", {})
        
        # 构建更详细的 prompt，包含 annotation 趋势
        daily_summary = "\n".join([f"  - {date}: {count} annotations" 
                                   for date, count in sorted(annotations_by_date.items())[:7]])
        
        prompt = f"""Based on the following project data, write a concise 2-3 paragraph summary of this week's progress. Focus on key achievements and insights. Write in English.

Project Data (Last 7 Days):
- Total annotations: {total_annotations}
- Total tasks: {total_tasks}
- Completed tasks: {completed_tasks}
- Completion rate: {completion_rate:.1f}%
- Active team members: {active_coders}

Daily annotation breakdown:
{daily_summary if daily_summary else "  - No detailed data available"}

Please write a brief, professional summary suitable for a weekly report."""

        messages = [
            {"role": "system", "content": "You are a professional project manager summarizing weekly progress. Keep it concise and insightful."},
            {"role": "user", "content": prompt}
        ]
        
        if LLM_PROVIDER in ("illinois_chat", "illinois", "uiuc_chat", "uiuc"):
            model = ILLINOIS_CHAT_MODEL
        else:
            model = DEFAULT_MODEL
        response = await call_llm(messages, model=model, force_json=False)
        
        content = response["choices"][0]["message"]["content"].strip()
        
        return {
            "success": True,
            "summary": content,
            "generated_at": datetime.utcnow().isoformat(),
            "metadata": {
                "model": model,
                "provider": response.get("provider") or LLM_PROVIDER,
                "period": f"{start_str} to {end_str}",
                "stats": {
                    "annotations": total_annotations,
                    "completion_rate": f"{completion_rate:.1f}%",
                    "active_coders": active_coders,
                    "completed_tasks": completed_tasks,
                    "total_tasks": total_tasks
                },
                "daily_breakdown": annotations_by_date,
                "team_performance": [
                    {
                        "name": m.get("name", "Unknown"),
                        "annotations": m.get("annotations_count", 0)
                    }
                    for m in team_members
                ]
            }
        }
        
    except Exception as e:
        print(f"❌ [Weekly Summary] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to generate summary: {str(e)}")


@router.post("/api/llm/monthly-report")
async def generate_monthly_report(
    request: MonthlyReportRequest,
    token: str = Query(...)
):
    """
    生成月报
    
    请求示例：
    ```json
    {
        "project_id": "65abc123...",
        "year": 2024,
        "month": 1,
        "model": "anthropic/claude-3.5-haiku"
    }
    ```
    
    响应格式（固定）：
    ```json
    {
        "success": true,
        "report": {
            "title": "月报标题",
            "period": "2024年1月",
            "summary": "执行摘要",
            "sections": {
                "monthly_progress": {...},
                "weekly_breakdown": [...],
                "team_performance": {...},
                "quality_metrics": {...},
                "achievements": [...],
                "challenges": [...],
                "next_month_plan": [...]
            },
            "conclusion": "总结"
        },
        "metadata": {
            "generated_at": "2024-02-01T10:00:00Z",
            "model_used": "anthropic/claude-3.5-haiku",
            "cost": 0.003
        }
    }
    ```
    """
    # 验证用户权限
    try:
        print(f"🔍 [Monthly Report] Received token: {token[:50]}..." if len(token) > 50 else f"🔍 [Monthly Report] Received token: {token}")
        user = verify_token(token)  # verify_token不是async函数
        print(f"✅ [Monthly Report] Token verified, user: {user}")
    except Exception as e:
        print(f"❌ [Monthly Report] Token verification failed: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # 计算月份的开始和结束日期
    import calendar
    from datetime import date
    
    start_date = date(request.year, request.month, 1)
    last_day = calendar.monthrange(request.year, request.month)[1]
    end_date = date(request.year, request.month, last_day)
    
    # 1. 从其他端口获取数据
    try:
        project_data = await fetch_project_data(
            request.project_id,
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d")
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch project data: {str(e)}"
        )
    
    # 2. 加载Prompt模板
    try:
        prompt_template = load_prompt_template("monthly_report")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load prompt template: {str(e)}"
        )
    
    # 3. 填充Prompt（类似weekly）
    prompt = prompt_template.format(
        project_name=project_data["project_name"],
        year=request.year,
        month=request.month,
        total_annotations=project_data["total_annotations"],
        total_tasks=project_data["total_tasks"],
        completed_tasks=project_data["completed_tasks"],
        team_members="\n".join([f"- {m['name']}: {m['annotations_count']} annotations" for m in project_data["team_members"]])
    )
    
    # 4. 调用LLM
    if LLM_PROVIDER in ("illinois_chat", "illinois", "uiuc_chat", "uiuc"):
        model = request.model or ILLINOIS_CHAT_MODEL
    else:
        model = request.model or DEFAULT_MODEL
    messages = [
        {
            "role": "system",
            "content": "You are a professional project management report writer. Always return valid JSON format."
        },
        {
            "role": "user",
            "content": prompt
        }
    ]
    
    try:
        response = await call_llm(
            messages=messages,
            model=model,
            temperature=0.5,
            max_tokens=4000,
            force_json=True,
        )
        
        content = response["choices"][0]["message"]["content"]
        report = _parse_llm_json(content)
        
        # 5. 返回固定格式
        return {
            "success": True,
            "report": report,
            "metadata": {
                "generated_at": datetime.utcnow().isoformat(),
                "model_used": model,
                "provider": response.get("provider") or LLM_PROVIDER,
                "cost": 0.003
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate monthly report: {str(e)}")


@router.post("/api/llm/annotate")
async def annotate_sentence(
    request: AnnotationRequest,
    token: str = Query(...)
):
    """
    Suggest codebook labels for selected content.
    Sends: content + all tag groups/options (with descriptions) + project memo.
    """
    try:
        user = verify_token(token)
        print(f"✅ [Annotate] Token verified, user: {user}")
    except Exception as e:
        print(f"❌ [Annotate] Token verification failed: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid token")

    content = (request.sentence or "").strip()
    if not content:
        raise HTTPException(
            status_code=400,
            detail="No content to annotate. Select or provide text for this task.",
        )
    if not request.tag_groups:
        raise HTTPException(status_code=400, detail="No tag groups provided")

    # Resolve project memo + optional LLM overrides from Settings
    project_memo = (request.project_memo or "").strip()
    project_api_key: Optional[str] = None
    project_annotation_model: Optional[str] = None
    if request.project_id:
        try:
            from bson import ObjectId
            core_db = get_core_db()
            proj = await core_db.projects.find_one(
                {"_id": ObjectId(request.project_id)},
                {"memo": 1, "name": 1, "description": 1, "llm_settings": 1},
            )
            if proj:
                if not project_memo:
                    project_memo = (
                        (proj.get("memo") or proj.get("description") or "").strip()
                        or f"Project: {proj.get('name') or request.project_id}"
                    )
                llm_cfg = proj.get("llm_settings") or {}
                llm_enabled = llm_cfg.get("llm_enabled", True)
                if llm_enabled is False:
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            "AI features are disabled for this project. "
                            "A manager can enable them under Settings → AI / OpenRouter."
                        ),
                    )
                project_api_key = (llm_cfg.get("openrouter_api_key") or "").strip() or None
                project_annotation_model = (
                    (llm_cfg.get("annotation_model") or llm_cfg.get("llm_model") or "").strip()
                    or None
                )
                # Without a project API key, only the platform basic model is allowed
                # (uses the server key / quota). Own key unlocks custom models + own quota.
                if not project_api_key:
                    project_annotation_model = None
        except HTTPException:
            raise
        except Exception as e:
            print(f"⚠️ [Annotate] Could not load project settings: {e}")

    if not project_memo:
        project_memo = "(No project memo provided.)"

    try:
        prompt_template = load_prompt_template("annotation")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load prompt template: {str(e)}"
        )

    tag_groups_block = _format_tag_groups_for_prompt(request.tag_groups)

    # Avoid str.format() — content may contain braces. Template uses single {placeholders}.
    prompt = (
        prompt_template
        .replace("{project_memo}", project_memo)
        .replace("{sentence}", content)
        .replace("{tag_groups}", tag_groups_block)
        .replace("{num_groups}", str(len(request.tag_groups)))
    )
    # Legacy templates may still use {{ }} from str.format era
    if "{{" in prompt or "}}" in prompt:
        prompt = prompt.replace("{{", "{").replace("}}", "}")

    # Prefer project Settings model when a project API key unlocks custom models.
    # request.model is only a fallback (clients should not hardcode).
    if project_api_key:
        model = project_annotation_model or request.model or ANNOTATION_MODEL
    else:
        model = ANNOTATION_MODEL
    print(f"🤖 [Annotate] model={model} content_len={len(content)} groups={len(request.tag_groups)}")
    messages = [
        {
            "role": "system",
            "content": (
                "You are a professional qualitative coding assistant. "
                "Always reply with valid JSON only."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    try:
        response = await call_openrouter(
            messages=messages,
            model=model,
            temperature=0.2,
            max_tokens=2000,
            force_json=True,
            api_key=project_api_key,
        )

        raw = response["choices"][0]["message"]["content"]
        print(f"🤖 [Annotate] raw response len={len(raw or '')}")
        annotation_result = _parse_llm_json(raw)

        # Normalize labels map: support dict keyed by group_id OR list
        labels_raw = annotation_result.get("labels") or {}
        if isinstance(labels_raw, list):
            labels_map = {
                (item.get("group_id") or ""): item
                for item in labels_raw
                if isinstance(item, dict)
            }
        else:
            labels_map = labels_raw if isinstance(labels_raw, dict) else {}

        # Allowed values per group for validation
        allowed: Dict[str, set] = {}
        for group in request.tag_groups:
            gid = group.get("group_id") or ""
            vals = set()
            for opt in group.get("options") or []:
                v = opt.get("value") or opt.get("option_id") or opt.get("label")
                if v:
                    vals.add(str(v))
                    if opt.get("label"):
                        vals.add(str(opt["label"]))  # tolerate label returns briefly
            allowed[gid] = vals

        labels = []
        for group in request.tag_groups:
            gid = group.get("group_id") or ""
            gname = group.get("group_name") or group.get("name") or gid
            gtype = group.get("type") or "single"
            group_result = labels_map.get(gid) or {}
            if not isinstance(group_result, dict):
                group_result = {}
            selected = group_result.get("selected") or []
            if not isinstance(selected, list):
                selected = [selected] if selected else []

            # Map labels → values when model returned display labels
            value_by_label = {}
            for opt in group.get("options") or []:
                val = str(opt.get("value") or opt.get("option_id") or opt.get("label") or "")
                lab = str(opt.get("label") or val)
                if lab:
                    value_by_label[lab.lower()] = val
                if val:
                    value_by_label[val.lower()] = val

            normalized = []
            for s in selected:
                key = str(s).strip()
                mapped = value_by_label.get(key.lower())
                if mapped:
                    normalized.append(mapped)
            # Deduplicate, respect single
            seen = set()
            deduped = []
            for v in normalized:
                if v not in seen:
                    seen.add(v)
                    deduped.append(v)
            if gtype == "single" and len(deduped) > 1:
                deduped = deduped[:1]

            conf = group_result.get("confidence", 0.5)
            try:
                conf = float(conf)
            except Exception:
                conf = 0.5

            labels.append({
                "group_id": gid,
                "group_name": gname,
                "selected": deduped,
                "confidence": conf,
            })

        overall = annotation_result.get("overall_confidence", 0.5)
        try:
            overall = float(overall)
        except Exception:
            overall = 0.5

        return {
            "success": True,
            "annotation": {
                "sentence": content,
                "labels": labels,
                "overall_confidence": overall,
                "reasoning": annotation_result.get("reasoning") or "",
            },
            "metadata": {
                "generated_at": datetime.utcnow().isoformat(),
                "model_used": model,
                "cost": 0.0,
            },
        }

    except HTTPException:
        raise
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Model returned invalid JSON: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to annotate: {str(e)}")


@router.post("/api/llm/suggest-codebook")
async def suggest_codebook(
    request: SuggestCodebookRequest,
    token: str = Query(...),
):
    """
    Suggest a codebook from recent tasks + optional user prompt.

    Response `tag_groups` is normalized to TagGroupCreate shape so the Code Book
    UI can preview / edit / apply without further remapping:
      group_id, name, description, type, required, order, active,
      options[{ option_id, label, order, active, description }]
    Does NOT write to the database.
    """
    try:
        verify_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    from bson import ObjectId

    project_id = (request.project_id or "").strip()
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")

    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project_id")

    core_db = get_core_db()
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    llm_cfg = project.get("llm_settings") or {}
    if llm_cfg.get("llm_enabled", True) is False:
        raise HTTPException(
            status_code=403,
            detail="AI features are disabled for this project (Settings → AI).",
        )

    project_db = await get_project_db(project_id)
    if project_db is None:
        raise HTTPException(status_code=404, detail="Project database not found")

    sample_size = max(1, min(int(request.sample_size or 100), 150))
    cursor = (
        project_db.tasks.find({})
        .sort([("created_at", -1)])
        .limit(sample_size)
    )
    tasks = await cursor.to_list(length=sample_size)

    samples: List[str] = []
    for idx, task in enumerate(tasks, start=1):
        samples.append(f"{idx}. [{task.get('task_type') or 'text'}] {_task_text_for_codebook(task)}")

    existing_docs = (
        await project_db.tag_groups.find({}).sort([("order", 1)]).to_list(length=200)
    )
    existing_for_prompt = [
        {
            "group_id": g.get("group_id"),
            "name": g.get("name"),
            "description": g.get("description"),
            "type": g.get("type"),
            "options": [
                {
                    "option_id": o.get("option_id"),
                    "label": o.get("label"),
                    "description": o.get("description"),
                }
                for o in (g.get("options") or [])
            ],
        }
        for g in existing_docs
    ]

    mode = (request.mode or "replace").strip().lower()
    if mode not in ("replace", "extend"):
        mode = "replace"

    user_prompt = (request.user_prompt or "").strip() or "(none — infer from samples)"
    project_context = (
        f"Name: {project.get('name') or project_id}\n"
        f"Memo: {(project.get('memo') or project.get('description') or '').strip() or '(none)'}"
    )

    template = load_prompt_template("suggest_codebook")
    prompt = (
        template.replace("{project_context}", project_context)
        .replace(
            "{existing_codebook}",
            _format_tag_groups_for_prompt(existing_for_prompt)
            if existing_for_prompt
            else "(empty — no tag groups yet)",
        )
        .replace(
            "{mode}",
            "extend — propose complementary groups, avoid duplicating existing ones"
            if mode == "extend"
            else "replace — propose a full codebook suitable for these materials",
        )
        .replace("{user_prompt}", user_prompt)
        .replace("{sample_count}", str(len(samples)))
        .replace(
            "{task_samples}",
            "\n".join(samples) if samples else "(no tasks uploaded yet — rely on user prompt)",
        )
    )

    if LLM_PROVIDER in ("illinois_chat", "illinois", "uiuc_chat", "uiuc"):
        model = request.model or ILLINOIS_CHAT_MODEL
    else:
        model = request.model or DEFAULT_MODEL

    messages = [
        {
            "role": "system",
            "content": (
                "You design qualitative coding codebooks. "
                "Reply with JSON only. The top-level key MUST be tag_groups, "
                "and each group MUST match the OpenCoder TagGroup schema."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    try:
        response = await call_llm(
            messages=messages,
            model=model,
            temperature=0.4,
            max_tokens=max(LLM_MAX_TOKENS, 4000),
            force_json=True,
        )
        content = response["choices"][0]["message"]["content"]
        parsed = _parse_llm_json(content)
        tag_groups = _normalize_suggested_tag_groups(parsed)
        rationale = ""
        if isinstance(parsed, dict):
            rationale = (parsed.get("rationale") or parsed.get("reasoning") or "").strip()

        return {
            "success": True,
            "tag_groups": tag_groups,
            "rationale": rationale,
            "metadata": {
                "generated_at": datetime.utcnow().isoformat(),
                "model_used": model,
                "provider": response.get("provider") or LLM_PROVIDER,
                "sampled_tasks": len(samples),
                "mode": mode,
            },
        }
    except HTTPException:
        raise
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(
            status_code=502,
            detail=f"Model returned invalid codebook JSON: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to suggest codebook: {str(e)}",
        )
