"""
数据导出路由
支持导出项目的标注数据、任务数据、用户统计等
"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from bson import ObjectId
import pandas as pd
import io
import zipfile
import logging

from database import get_core_db, get_project_db
from routers.auth import verify_token

router = APIRouter()
logger = logging.getLogger(__name__)


async def verify_manager(user: Dict[str, Any], project_id: str):
    """验证用户是否为项目的 Manager"""
    core_db = get_core_db()
    
    try:
        project_oid = ObjectId(project_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 检查是否是项目的 owner
    # JWT payload 中用户 ID 字段名是 "sub"
    user_id = user.get("sub") or user.get("id")
    if str(project.get("owner_user_id")) != user_id:
        raise HTTPException(status_code=403, detail="Only project manager can export data")
    
    return project


@router.get("/{project_id}/annotations/csv")
async def export_annotations_csv(
    project_id: str,
    token: str = Query(...),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)")
):
    """
    导出项目所有标注数据为 CSV
    需要 Manager 权限
    """
    user = verify_token(token)
    await verify_manager(user, project_id)
    
    core_db = get_core_db()
    project_db = await get_project_db(project_id)
    
    # 构建日期过滤条件
    date_filter = {}
    if start_date or end_date:
        date_filter["completed_at"] = {}
        if start_date:
            date_filter["completed_at"]["$gte"] = datetime.fromisoformat(start_date)
        if end_date:
            date_filter["completed_at"]["$lte"] = datetime.fromisoformat(end_date) + timedelta(days=1)
    
    # 获取所有标注数据
    annotations = await project_db.annotations.find(date_filter).to_list(None)
    
    if not annotations:
        raise HTTPException(status_code=404, detail="No annotations found")
    
    # 准备数据
    rows = []
    for ann in annotations:
        # 获取标注员信息
        coder = await core_db.users.find_one({"_id": ann["coder_user_id"]})
        coder_name = coder.get("name", "Unknown") if coder else "Unknown"
        coder_email = coder.get("email", "") if coder else ""
        
        # 获取任务信息
        task = await project_db.tasks.find_one({"_id": ann["task_id"]})
        task_title = task.get("title", "") if task else ""
        task_type = task.get("task_type", "") if task else ""
        
        # 处理标签数据
        labels = ann.get("labels", [])
        labels_str = ""
        if isinstance(labels, list):
            labels_parts = []
            for label_item in labels:
                if isinstance(label_item, dict):
                    group_id = label_item.get("group_id", "")
                    option_ids = label_item.get("option_ids", [])
                    labels_parts.append(f"{group_id}:{','.join(option_ids)}")
            labels_str = "; ".join(labels_parts)
        
        row = {
            "annotation_id": str(ann["_id"]),
            "task_id": str(ann["task_id"]),
            "task_title": task_title,
            "task_type": task_type,
            "coder_id": str(ann["coder_user_id"]),
            "coder_name": coder_name,
            "coder_email": coder_email,
            "labels": labels_str,
            "note": ann.get("note", ""),
            "created_at": ann.get("created_at", "").isoformat() if ann.get("created_at") else "",
            "completed_at": ann.get("completed_at", "").isoformat() if ann.get("completed_at") else "",
        }
        rows.append(row)
    
    # 创建 DataFrame
    df = pd.DataFrame(rows)
    
    # 转换为 CSV
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')  # utf-8-sig for Excel compatibility
    csv_buffer.seek(0)
    
    # 返回 CSV 文件
    filename = f"annotations_{project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        io.BytesIO(csv_buffer.getvalue().encode('utf-8-sig')),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/{project_id}/tasks/csv")
async def export_tasks_csv(
    project_id: str,
    token: str = Query(...)
):
    """
    导出项目所有任务数据为 CSV
    需要 Manager 权限
    """
    user = verify_token(token)
    await verify_manager(user, project_id)
    
    project_db = await get_project_db(project_id)
    
    # 获取所有任务
    tasks = await project_db.tasks.find({}).to_list(None)
    
    if not tasks:
        raise HTTPException(status_code=404, detail="No tasks found")
    
    # 准备数据
    rows = []
    for task in tasks:
        # 获取标注数
        annotations_count = await project_db.annotations.count_documents({"task_id": task["_id"]})
        
        # 获取分配数
        assignments_count = await project_db.assignments.count_documents({"task_id": task["_id"]})
        
        # 处理 payload
        payload = task.get("payload", {})
        task_text = payload.get("text", "")
        task_url = payload.get("url", "")
        
        # 处理图片信息
        image_info = ""
        if task.get("task_type") == "image" and payload.get("image"):
            img = payload["image"]
            image_info = f"{img.get('original_filename', '')} ({img.get('drive_file_id', '')})"
        
        row = {
            "task_id": str(task["_id"]),
            "title": task.get("title", ""),
            "task_type": task.get("task_type", ""),
            "status": task.get("status", ""),
            "text": task_text[:200] if task_text else "",  # 限制长度
            "url": task_url,
            "image_info": image_info,
            "tags": ", ".join(task.get("tags", [])),
            "annotations_count": annotations_count,
            "assignments_count": assignments_count,
            "created_at": task.get("created_at", "").isoformat() if task.get("created_at") else "",
            "updated_at": task.get("updated_at", "").isoformat() if task.get("updated_at") else "",
        }
        rows.append(row)
    
    # 创建 DataFrame
    df = pd.DataFrame(rows)
    
    # 转换为 CSV
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
    csv_buffer.seek(0)
    
    # 返回 CSV 文件
    filename = f"tasks_{project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        io.BytesIO(csv_buffer.getvalue().encode('utf-8-sig')),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/{project_id}/annotations/excel")
async def export_annotations_excel(
    project_id: str,
    token: str = Query(...),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)")
):
    """
    导出项目所有标注数据为 Excel
    包含多个 sheet：标注详情、任务汇总、标注员统计
    需要 Manager 权限
    """
    user = verify_token(token)
    await verify_manager(user, project_id)
    
    core_db = get_core_db()
    project_db = await get_project_db(project_id)
    
    # 构建日期过滤条件
    date_filter = {}
    if start_date or end_date:
        date_filter["completed_at"] = {}
        if start_date:
            date_filter["completed_at"]["$gte"] = datetime.fromisoformat(start_date)
        if end_date:
            date_filter["completed_at"]["$lte"] = datetime.fromisoformat(end_date) + timedelta(days=1)
    
    # 获取所有标注数据
    annotations = await project_db.annotations.find(date_filter).to_list(None)
    
    if not annotations:
        raise HTTPException(status_code=404, detail="No annotations found")
    
    # 获取所有 tag groups，用于创建列标题
    tag_groups = await project_db.tag_groups.find({}).to_list(None)
    tag_group_map = {}
    for tg in tag_groups:
        group_id = tg.get("group_id", "")
        group_name = tg.get("name", group_id)
        tag_group_map[group_id] = group_name
    
    # Sheet 1: 标注详情
    annotation_rows = []
    for ann in annotations:
        # 获取标注员信息
        coder = await core_db.users.find_one({"_id": ann["coder_user_id"]})
        coder_name = coder.get("name", "Unknown") if coder else "Unknown"
        coder_email = coder.get("email", "") if coder else ""
        
        # 获取任务信息
        task = await project_db.tasks.find_one({"_id": ann["task_id"]})
        task_title = task.get("title", "") if task else ""
        task_type = task.get("task_type", "") if task else ""
        task_text = ""
        if task:
            payload = task.get("payload")
            if payload and isinstance(payload, dict):
                text = payload.get("text", "")
                task_text = text[:500] if text else ""
        
        # 处理标签数据 - 每个 group 一个列
        labels = ann.get("labels", "")
        labels_dict = {}
        
        # 初始化所有 tag group 列为空
        for group_id, group_name in tag_group_map.items():
            labels_dict[group_name] = ""
        
        # 解析标签字符串
        # 格式: "sentiment_1761100212558:No Sentiment; support_system_1761100298583:School"
        if isinstance(labels, str) and labels:
            # 按 ; 分割多个标签
            label_parts = labels.split(';')
            for part in label_parts:
                part = part.strip()
                if ':' in part:
                    # 分割 group_id_timestamp 和 value
                    key, value = part.split(':', 1)
                    # 去掉 timestamp 部分 (group_id_timestamp -> group_id)
                    # 例如: sentiment_1761100212558 -> sentiment
                    group_id = '_'.join(key.split('_')[:-1]) if '_' in key else key
                    # 使用 group name 作为列名（如果有映射），否则用 group_id
                    column_name = tag_group_map.get(group_id, group_id)
                    labels_dict[column_name] = value.strip()
        elif isinstance(labels, list):
            # 兼容列表格式（如果有的话）
            for label_item in labels:
                if isinstance(label_item, dict):
                    group_id = label_item.get("group_id", "")
                    option_ids = label_item.get("option_ids", [])
                    group_name = tag_group_map.get(group_id, group_id)
                    labels_dict[group_name] = ", ".join(option_ids)
        
        row = {
            "Annotation ID": str(ann["_id"]),
            "Task ID": str(ann["task_id"]),
            "Task Title": task_title,
            "Task Type": task_type,
            "Task Text": task_text,
            "Coder Name": coder_name,
            "Coder Email": coder_email,
            **labels_dict,  # 每个 tag group 一列
            "Note": ann.get("note", ""),
            "Created At": ann.get("created_at", "").isoformat() if ann.get("created_at") else "",
            "Completed At": ann.get("completed_at", "").isoformat() if ann.get("completed_at") else "",
        }
        annotation_rows.append(row)
    
    df_annotations = pd.DataFrame(annotation_rows)
    
    # Sheet 2: 标注员统计
    coder_stats_pipeline = [
        {
            "$match": date_filter if date_filter else {}
        },
        {
            "$group": {
                "_id": "$coder_user_id",
                "total_annotations": {"$sum": 1},
                "unique_tasks": {"$addToSet": "$task_id"},
                "first_annotation": {"$min": "$completed_at"},
                "last_annotation": {"$max": "$completed_at"}
            }
        }
    ]
    
    coder_stats = await project_db.annotations.aggregate(coder_stats_pipeline).to_list(None)
    
    coder_rows = []
    for stat in coder_stats:
        coder = await core_db.users.find_one({"_id": stat["_id"]})
        coder_name = coder.get("name", "Unknown") if coder else "Unknown"
        coder_email = coder.get("email", "") if coder else ""
        
        coder_rows.append({
            "Coder ID": str(stat["_id"]),
            "Coder Name": coder_name,
            "Coder Email": coder_email,
            "Total Annotations": stat["total_annotations"],
            "Unique Tasks": len(stat["unique_tasks"]),
            "First Annotation": stat.get("first_annotation", "").isoformat() if stat.get("first_annotation") else "",
            "Last Annotation": stat.get("last_annotation", "").isoformat() if stat.get("last_annotation") else ""
        })
    
    df_coders = pd.DataFrame(coder_rows)
    
    # Sheet 3: 任务汇总
    tasks = await project_db.tasks.find({}).to_list(None)
    task_rows = []
    for task in tasks:
        annotations_count = await project_db.annotations.count_documents({"task_id": task["_id"]})
        
        payload = task.get("payload", {})
        task_text = payload.get("text", "")[:200] if payload.get("text") else ""
        
        task_rows.append({
            "Task ID": str(task["_id"]),
            "Title": task.get("title", ""),
            "Type": task.get("task_type", ""),
            "Status": task.get("status", ""),
            "Text": task_text,
            "Tags": ", ".join(task.get("tags", [])),
            "Annotations Count": annotations_count,
            "Created At": task.get("created_at", "").isoformat() if task.get("created_at") else ""
        })
    
    df_tasks = pd.DataFrame(task_rows)
    
    # 创建 Excel 文件（多个 sheet）
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
        df_annotations.to_excel(writer, sheet_name='Annotations', index=False)
        df_coders.to_excel(writer, sheet_name='Coder Statistics', index=False)
        df_tasks.to_excel(writer, sheet_name='Tasks Summary', index=False)
        
        # 调整列宽
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
    
    excel_buffer.seek(0)
    
    # 返回 Excel 文件
    filename = f"project_data_{project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        excel_buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/{project_id}/complete-export")
async def export_complete_data(
    project_id: str,
    token: str = Query(...),
    format: str = Query("excel", description="Export format: 'excel' or 'zip'")
):
    """
    导出项目完整数据
    - format='excel': 单个 Excel 文件，包含多个 sheet
    - format='zip': ZIP 压缩包，包含多个 CSV 文件
    需要 Manager 权限
    """
    user = verify_token(token)
    await verify_manager(user, project_id)
    
    core_db = get_core_db()
    project_db = await get_project_db(project_id)
    
    # 获取项目信息
    try:
        project_oid = ObjectId(project_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 获取所有 tag groups，用于创建列标题
    tag_groups = await project_db.tag_groups.find({}).to_list(None)
    tag_group_map = {}
    for tg in tag_groups:
        group_id = tg.get("group_id", "")
        group_name = tg.get("name", group_id)
        tag_group_map[group_id] = group_name
    
    # 1. 获取所有标注数据
    annotations = await project_db.annotations.find({}).to_list(None)
    annotation_rows = []
    for ann in annotations:
        coder = await core_db.users.find_one({"_id": ann["coder_user_id"]})
        coder_name = coder.get("name", "Unknown") if coder else "Unknown"
        coder_email = coder.get("email", "") if coder else ""
        
        task = await project_db.tasks.find_one({"_id": ann["task_id"]})
        task_title = task.get("title", "") if task else ""
        task_type = task.get("task_type", "") if task else ""
        task_text = ""
        if task:
            payload = task.get("payload")
            if payload and isinstance(payload, dict):
                text = payload.get("text", "")
                task_text = text[:500] if text else ""
        
        # 处理标签数据 - 每个 group 一个列
        labels = ann.get("labels", "")
        labels_dict = {}
        
        # 初始化所有 tag group 列为空
        for group_id, group_name in tag_group_map.items():
            labels_dict[group_name] = ""
        
        # 解析标签字符串
        # 格式: "sentiment_1761100212558:No Sentiment; support_system_1761100298583:School"
        if isinstance(labels, str) and labels:
            # 按 ; 分割多个标签
            label_parts = labels.split(';')
            for part in label_parts:
                part = part.strip()
                if ':' in part:
                    # 分割 group_id_timestamp 和 value
                    key, value = part.split(':', 1)
                    # 去掉 timestamp 部分 (group_id_timestamp -> group_id)
                    group_id = '_'.join(key.split('_')[:-1]) if '_' in key else key
                    # 使用 group name 作为列名
                    column_name = tag_group_map.get(group_id, group_id)
                    labels_dict[column_name] = value.strip()
        elif isinstance(labels, list):
            # 兼容列表格式
            for label_item in labels:
                if isinstance(label_item, dict):
                    group_id = label_item.get("group_id", "")
                    option_ids = label_item.get("option_ids", [])
                    group_name = tag_group_map.get(group_id, group_id)
                    labels_dict[group_name] = ", ".join(option_ids)
        
        annotation_rows.append({
            "Annotation ID": str(ann["_id"]),
            "Task ID": str(ann["task_id"]),
            "Task Title": task_title,
            "Task Type": task_type,
            "Task Text": task_text,
            "Coder Name": coder_name,
            "Coder Email": coder_email,
            **labels_dict,  # 每个 tag group 独立列
            "Note": ann.get("note", ""),
            "Created At": ann.get("created_at", "").isoformat() if ann.get("created_at") else "",
            "Completed At": ann.get("completed_at", "").isoformat() if ann.get("completed_at") else "",
        })
    
    df_annotations = pd.DataFrame(annotation_rows)
    
    # 2. 获取所有任务数据
    tasks = await project_db.tasks.find({}).to_list(None)
    task_rows = []
    for task in tasks:
        annotations_count = await project_db.annotations.count_documents({"task_id": task["_id"]})
        assignments_count = await project_db.assignments.count_documents({"task_id": task["_id"]})
        
        payload = task.get("payload", {})
        task_text = payload.get("text", "")
        task_url = payload.get("url", "")
        
        image_info = ""
        if task.get("task_type") == "image" and payload.get("image"):
            img = payload["image"]
            image_info = f"{img.get('original_filename', '')} | {img.get('drive_file_url', '')}"
        
        task_rows.append({
            "Task ID": str(task["_id"]),
            "Title": task.get("title", ""),
            "Type": task.get("task_type", ""),
            "Status": task.get("status", ""),
            "Text": task_text,
            "URL": task_url,
            "Image Info": image_info,
            "Tags": ", ".join(task.get("tags", [])),
            "Annotations Count": annotations_count,
            "Assignments Count": assignments_count,
            "Created At": task.get("created_at", "").isoformat() if task.get("created_at") else ""
        })
    
    df_tasks = pd.DataFrame(task_rows)
    
    # 3. 获取标注员统计
    coder_stats_pipeline = [
        {
            "$group": {
                "_id": "$coder_user_id",
                "total_annotations": {"$sum": 1},
                "unique_tasks": {"$addToSet": "$task_id"},
                "first_annotation": {"$min": "$completed_at"},
                "last_annotation": {"$max": "$completed_at"}
            }
        }
    ]
    coder_stats = await project_db.annotations.aggregate(coder_stats_pipeline).to_list(None)
    
    coder_rows = []
    for stat in coder_stats:
        coder = await core_db.users.find_one({"_id": stat["_id"]})
        coder_name = coder.get("name", "Unknown") if coder else "Unknown"
        coder_email = coder.get("email", "") if coder else ""
        
        assignments_count = await project_db.assignments.count_documents({"coder_user_id": stat["_id"]})
        
        coder_rows.append({
            "Coder ID": str(stat["_id"]),
            "Coder Name": coder_name,
            "Coder Email": coder_email,
            "Total Annotations": stat["total_annotations"],
            "Unique Tasks": len(stat["unique_tasks"]),
            "Total Assignments": assignments_count,
            "First Annotation": stat.get("first_annotation", "").isoformat() if stat.get("first_annotation") else "",
            "Last Annotation": stat.get("last_annotation", "").isoformat() if stat.get("last_annotation") else ""
        })
    
    df_coders = pd.DataFrame(coder_rows)
    
    # 4. 获取标签组信息
    tag_groups = await project_db.tag_groups.find({}).to_list(None)
    tag_group_rows = []
    for tg in tag_groups:
        options_str = ", ".join([opt.get("label", "") for opt in tg.get("options", [])])
        tag_group_rows.append({
            "Group ID": tg.get("group_id", ""),
            "Name": tg.get("name", ""),
            "Type": tg.get("type", ""),
            "Required": tg.get("required", False),
            "Options": options_str
        })
    
    df_tag_groups = pd.DataFrame(tag_group_rows)
    
    if format == "zip":
        # 创建 ZIP 文件
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # 添加各个 CSV 文件
            for name, df in [
                ("annotations", df_annotations),
                ("tasks", df_tasks),
                ("coders", df_coders),
                ("tag_groups", df_tag_groups)
            ]:
                csv_buffer = io.StringIO()
                df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
                zip_file.writestr(f"{name}.csv", csv_buffer.getvalue().encode('utf-8-sig'))
            
            # 添加项目信息文件
            project_info = f"""Project Export
Project ID: {project_id}
Project Name: {project.get('name', 'Unknown')}
Export Date: {datetime.now().isoformat()}
Total Annotations: {len(annotations)}
Total Tasks: {len(tasks)}
Total Coders: {len(coder_stats)}
"""
            zip_file.writestr("README.txt", project_info.encode('utf-8'))
        
        zip_buffer.seek(0)
        filename = f"project_export_{project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    else:
        # 创建 Excel 文件（默认）
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
            df_annotations.to_excel(writer, sheet_name='Annotations', index=False)
            df_tasks.to_excel(writer, sheet_name='Tasks', index=False)
            df_coders.to_excel(writer, sheet_name='Coder Statistics', index=False)
            df_tag_groups.to_excel(writer, sheet_name='Tag Groups', index=False)
            
            # 添加项目信息 sheet
            project_info_df = pd.DataFrame([{
                "Project ID": project_id,
                "Project Name": project.get("name", "Unknown"),
                "Export Date": datetime.now().isoformat(),
                "Total Annotations": len(annotations),
                "Total Tasks": len(tasks),
                "Total Coders": len(coder_stats)
            }])
            project_info_df.to_excel(writer, sheet_name='Project Info', index=False)
            
            # 调整列宽
            for sheet_name in writer.sheets:
                worksheet = writer.sheets[sheet_name]
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    adjusted_width = min(max_length + 2, 60)
                    worksheet.column_dimensions[column_letter].width = adjusted_width
        
        excel_buffer.seek(0)
        
        filename = f"project_complete_export_{project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return StreamingResponse(
            excel_buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )


@router.get("/{project_id}/team-statistics/csv")
async def export_team_statistics_csv(
    project_id: str,
    token: str = Query(...),
    days: int = Query(30, description="Number of days to look back")
):
    """
    导出团队统计数据为 CSV
    需要 Manager 权限
    """
    user = verify_token(token)
    await verify_manager(user, project_id)
    
    core_db = get_core_db()
    project_db = await get_project_db(project_id)
    
    # 计算日期范围
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)
    date_filter = {"$gte": start_date, "$lt": end_date}
    
    # 获取每个 coder 的统计
    coder_stats_pipeline = [
        {
            "$match": {"completed_at": date_filter}
        },
        {
            "$group": {
                "_id": "$coder_user_id",
                "total_annotations": {"$sum": 1},
                "unique_tasks": {"$addToSet": "$task_id"},
                "first_annotation": {"$min": "$completed_at"},
                "last_annotation": {"$max": "$completed_at"}
            }
        },
        {
            "$sort": {"total_annotations": -1}
        }
    ]
    
    coder_stats = await project_db.annotations.aggregate(coder_stats_pipeline).to_list(None)
    
    rows = []
    for i, stat in enumerate(coder_stats, 1):
        coder = await core_db.users.find_one({"_id": stat["_id"]})
        coder_name = coder.get("name", "Unknown") if coder else "Unknown"
        coder_email = coder.get("email", "") if coder else ""
        
        # 计算活跃天数
        active_days = len(await project_db.annotations.distinct(
            "completed_at",
            {"coder_user_id": stat["_id"], "completed_at": date_filter}
        ))
        
        # 计算平均每日标注数
        avg_daily = stat["total_annotations"] / max(active_days, 1)
        
        rows.append({
            "Rank": i,
            "Coder Name": coder_name,
            "Coder Email": coder_email,
            "Total Annotations": stat["total_annotations"],
            "Unique Tasks": len(stat["unique_tasks"]),
            "Active Days": active_days,
            "Avg Daily Annotations": round(avg_daily, 1),
            "First Annotation": stat.get("first_annotation", "").isoformat() if stat.get("first_annotation") else "",
            "Last Annotation": stat.get("last_annotation", "").isoformat() if stat.get("last_annotation") else ""
        })
    
    df = pd.DataFrame(rows)
    
    # 转换为 CSV
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
    csv_buffer.seek(0)
    
    filename = f"team_statistics_{project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        io.BytesIO(csv_buffer.getvalue().encode('utf-8-sig')),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
