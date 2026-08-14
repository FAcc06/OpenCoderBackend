"""
LLM Router - Using OpenRouter with Prompt Templates
LLM路由，使用OpenRouter和外部Prompt模板
"""
from fastapi import APIRouter, HTTPException
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
    """标注请求"""
    sentence: str
    tag_groups: List[Dict]  # 从其他端口获取的标签组配置
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
    max_tokens: int = LLM_MAX_TOKENS
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
        "response_format": {"type": "json_object"}  # 强制JSON输出
    }
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM call failed: {str(e)}")


async def fetch_project_data(project_id: str, start_date: str, end_date: str) -> Dict:
    """
    从其他端口获取项目数据
    
    这个函数调用您现有的API端点来获取数据
    """
    core_db = get_core_db()
    project_db = await get_project_db(project_id)
    
    if not project_db:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 解析日期
    from datetime import datetime as dt
    start_dt = dt.strptime(start_date, "%Y-%m-%d")
    end_dt = dt.strptime(end_date, "%Y-%m-%d")
    
    # 获取数据
    annotations = await project_db.annotations.find({
        "created_at": {"$gte": start_dt, "$lte": end_dt}
    }).to_list(None)
    
    tasks = await project_db.tasks.find({
        "created_at": {"$gte": start_dt, "$lte": end_dt}
    }).to_list(None)
    
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
        "project_name": project.get("name", "项目") if project else "项目",
        "period": f"{start_date} 至 {end_date}",
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
    token: str
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
        user = await verify_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # 1. 从其他端口获取数据
    try:
        project_data = await fetch_project_data(
            request.project_id,
            request.start_date,
            request.end_date
        )
    except Exception as e:
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
        team_members="\n".join([f"- {m['name']}: {m['annotations_count']}个标注" for m in project_data["team_members"]]),
        annotations_by_date="\n".join([f"- {date}: {count}个" for date, count in project_data["annotations_by_date"].items()])
    )
    
    # 4. 调用LLM
    model = request.model or DEFAULT_MODEL
    messages = [
        {
            "role": "system",
            "content": "你是一个专业的项目管理报告撰写专家。请始终返回有效的JSON格式。"
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


@router.post("/api/llm/monthly-report")
async def generate_monthly_report(
    request: MonthlyReportRequest,
    token: str
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
        user = await verify_token(token)
    except Exception:
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
        team_members="\n".join([f"- {m['name']}: {m['annotations_count']}个标注" for m in project_data["team_members"]])
    )
    
    # 4. 调用LLM
    model = request.model or DEFAULT_MODEL
    messages = [
        {
            "role": "system",
            "content": "你是一个专业的项目管理报告撰写专家。请始终返回有效的JSON格式。"
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
    token: str
):
    """
    对句子进行标注建议
    
    请求示例：
    ```json
    {
        "sentence": "这部电影非常精彩，值得一看！",
        "tag_groups": [
            {
                "group_id": "sentiment",
                "group_name": "情感倾向",
                "type": "single",
                "options": [
                    {"value": "positive", "label": "正面"},
                    {"value": "negative", "label": "负面"},
                    {"value": "neutral", "label": "中立"}
                ]
            },
            {
                "group_id": "topic",
                "group_name": "话题分类",
                "type": "multi",
                "options": [
                    {"value": "entertainment", "label": "娱乐"},
                    {"value": "movie", "label": "电影"},
                    {"value": "recommendation", "label": "推荐"}
                ]
            }
        ],
        "model": "anthropic/claude-3.5-haiku"
    }
    ```
    
    响应格式（固定）：
    ```json
    {
        "success": true,
        "annotation": {
            "sentence": "这部电影非常精彩，值得一看！",
            "labels": [
                {
                    "group_id": "sentiment",
                    "group_name": "情感倾向",
                    "selected": ["positive"],
                    "confidence": 0.95
                },
                {
                    "group_id": "topic",
                    "group_name": "话题分类",
                    "selected": ["entertainment", "movie", "recommendation"],
                    "confidence": 0.88
                }
            ],
            "overall_confidence": 0.92,
            "reasoning": "文本明确表达了对电影的正面评价..."
        },
        "metadata": {
            "generated_at": "2024-01-20T10:00:00Z",
            "model_used": "anthropic/claude-3.5-haiku",
            "cost": 0.0003
        }
    }
    ```
    """
    # 验证用户权限
    try:
        user = await verify_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # 1. 加载Prompt模板
    try:
        prompt_template = load_prompt_template("annotation")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load prompt template: {str(e)}"
        )
    
    # 2. 构建标签组描述
    tag_groups_desc = []
    for group in request.tag_groups:
        options_str = ", ".join([f"{opt['value']}({opt['label']})" for opt in group["options"]])
        tag_groups_desc.append(
            f"- {group['group_name']} ({group['type']}): {options_str}"
        )
    
    # 3. 填充Prompt
    prompt = prompt_template.format(
        sentence=request.sentence,
        tag_groups="\n".join(tag_groups_desc),
        num_groups=len(request.tag_groups)
    )
    
    # 4. 调用LLM
    model = request.model or DEFAULT_MODEL
    messages = [
        {
            "role": "system",
            "content": "你是一个专业的文本标注专家。请始终返回有效的JSON格式。"
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
            temperature=0.3,
            max_tokens=1000
        )
        
        content = response["choices"][0]["message"]["content"]
        annotation_result = json.loads(content)
        
        # 5. 格式化为固定输出格式
        labels = []
        for group in request.tag_groups:
            group_result = annotation_result.get("labels", {}).get(group["group_id"], {})
            labels.append({
                "group_id": group["group_id"],
                "group_name": group["group_name"],
                "selected": group_result.get("selected", []),
                "confidence": group_result.get("confidence", 0.5)
            })
        
        return {
            "success": True,
            "annotation": {
                "sentence": request.sentence,
                "labels": labels,
                "overall_confidence": annotation_result.get("overall_confidence", 0.5),
                "reasoning": annotation_result.get("reasoning", "")
            },
            "metadata": {
                "generated_at": datetime.utcnow().isoformat(),
                "model_used": model,
                "cost": 0.0003
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to annotate: {str(e)}")
