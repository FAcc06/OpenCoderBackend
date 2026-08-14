"""
LLM Router - Using OpenRouter with Prompt Templates
LLM路由，使用OpenRouter和外部Prompt模板
"""
from fastapi import APIRouter, HTTPException, Query
from typing import List, Dict, Optional
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
# Annotate uses a reliable general chat model unless overridden
ANNOTATION_MODEL = os.getenv("ANNOTATION_MODEL", DEFAULT_MODEL)

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
) -> Dict:
    """调用OpenRouter API"""
    if not OPENROUTER_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="OPENROUTER_API_KEY not configured"
        )
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
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
    
    # 4. 调用LLM
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
        response = await call_openrouter(
            messages=messages,
            model=model,
            temperature=0.5,
            max_tokens=3000
        )
        
        content = response["choices"][0]["message"]["content"]
        report = json.loads(content)
        
        # 5. 返回固定格式
        return {
            "success": True,
            "report": report,
            "metadata": {
                "generated_at": datetime.utcnow().isoformat(),
                "model_used": model,
                "cost": 0.002  # 简化版，实际应计算
            }
        }
        
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse LLM response as JSON")
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
        
        # 使用 LLM 生成简洁总结
        model = "anthropic/claude-3.5-haiku"
        
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
        
        response = await call_openrouter(messages, model=model)
        
        content = response["choices"][0]["message"]["content"].strip()
        
        return {
            "success": True,
            "summary": content,
            "generated_at": datetime.utcnow().isoformat(),
            "metadata": {
                "model": model,
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
        response = await call_openrouter(
            messages=messages,
            model=model,
            temperature=0.5,
            max_tokens=4000
        )
        
        content = response["choices"][0]["message"]["content"]
        report = json.loads(content)
        
        # 5. 返回固定格式
        return {
            "success": True,
            "report": report,
            "metadata": {
                "generated_at": datetime.utcnow().isoformat(),
                "model_used": model,
                "cost": 0.003
            }
        }
        
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

    # Resolve project memo (request override, else load from DB)
    project_memo = (request.project_memo or "").strip()
    if not project_memo and request.project_id:
        try:
            from bson import ObjectId
            core_db = get_core_db()
            proj = await core_db.projects.find_one(
                {"_id": ObjectId(request.project_id)},
                {"memo": 1, "name": 1, "description": 1},
            )
            if proj:
                project_memo = (
                    (proj.get("memo") or proj.get("description") or "").strip()
                    or f"Project: {proj.get('name') or request.project_id}"
                )
        except Exception as e:
            print(f"⚠️ [Annotate] Could not load project memo: {e}")

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

    # Avoid str.format() — content may contain braces
    prompt = (
        prompt_template
        .replace("{project_memo}", project_memo)
        .replace("{sentence}", content)
        .replace("{tag_groups}", tag_groups_block)
        .replace("{num_groups}", str(len(request.tag_groups)))
    )

    model = request.model or ANNOTATION_MODEL
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
        )

        raw = response["choices"][0]["message"]["content"]
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
