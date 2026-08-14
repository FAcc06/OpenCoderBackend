from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from pydantic import BaseModel
from typing import List, Optional
from bson import ObjectId
from datetime import datetime, timedelta
import io
import logging
import json

from database import get_core_db, get_project_db
from models import (
    PDFDocument, PDFDocumentCreate,
    DocumentLevelCoding, DocumentLevelCodingCreate, DocumentLevelCodingUpdate,
    PassageAnnotation, PassageAnnotationCreate, PassageAnnotationUpdate,
    TaskType, Task, TaskPayload, PDFData, RectangleCoordinate
)
from routers.auth import verify_token
from services.google_drive import GoogleDriveService

logger = logging.getLogger(__name__)
router = APIRouter()

# ── PDF text extraction ───────────────────────────────────────────────────────

MAX_EXTRACT_CHARS = 80_000   # cap sent to LLM (pypdf may return more for long docs)

def extract_pdf_text(file_bytes: bytes) -> str:
    """
    Extract plain text from PDF bytes.
    Tries pypdf first, falls back to pdfminer.six for complex font encodings.
    Returns empty string only if both fail.
    """
    # ── Try pypdf first (fast) ────────────────────────────────────────────────
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        n_pages = len(reader.pages)
        logger.info("pypdf: PDF has %d pages", n_pages)
        parts = []
        for i, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
                parts.append(text)
            except Exception as page_err:
                logger.warning("pypdf: page %d error: %s", i, page_err)
        combined = "\n".join(parts).strip()
        logger.info("pypdf: extracted %d chars", len(combined))
        if combined:
            return combined
        logger.info("pypdf returned empty — trying pdfminer.six fallback")
    except Exception as e:
        logger.warning("pypdf failed: %s — trying pdfminer.six fallback", e)

    # ── Fallback: pdfminer.six (handles more font encodings) ─────────────────
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        combined = pdfminer_extract(io.BytesIO(file_bytes)).strip()
        logger.info("pdfminer.six: extracted %d chars", len(combined))
        return combined
    except Exception as e:
        logger.warning("pdfminer.six also failed: %s", e)
        return ""


@router.post("/{project_id}/pdf-coding/upload")
async def upload_pdf_document(
    project_id: str,
    title: str = Form(...),
    tags: str = Form("[]"),
    pdf: UploadFile = File(...),
    token: str = Query(...)
):
    """
    上传 PDF 文件并创建 PDF Document Coding 任务
    
    流程：
    1. 验证用户权限
    2. 验证 PDF 文件
    3. 上传到 Manager 的 Google Drive
    4. 创建 Task 和 PDF Document 记录
    5. 生成唯一 Task ID
    """
    # 验证用户
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    core_db = get_core_db()
    
    # 检查项目是否存在
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 验证用户是否属于该项目
    user = await core_db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if str(user.get("project_id")) != project_id:
        raise HTTPException(status_code=403, detail="User not in this project")
    
    # 验证文件类型
    if not pdf.content_type or pdf.content_type != 'application/pdf':
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    if not pdf.filename or not pdf.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must have .pdf extension")
    
    # 读取文件内容
    try:
        file_content = await pdf.read()
        file_size = len(file_content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {str(e)}")

    # 提取文本（内存中完成，毫秒级，非阻塞）
    extracted_text = extract_pdf_text(file_content)
    logger.info("Extracted %d chars from PDF %s", len(extracted_text), pdf.filename)
    
    # 限制文件大小 (50MB)
    if file_size > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="PDF size exceeds 50MB limit")
    
    logger.info(f"User {user_id} uploading PDF: {pdf.filename} ({file_size} bytes)")
    
    # 获取 Manager 的 Google Drive credentials
    manager_id = project.get("owner_user_id")
    if not manager_id:
        raise HTTPException(status_code=500, detail="Project manager not found")
    
    manager = await core_db.users.find_one({"_id": manager_id})
    if not manager:
        raise HTTPException(status_code=404, detail="Manager not found")
    
    google_creds = manager.get("google_credentials")
    if not google_creds or not google_creds.get("access_token"):
        raise HTTPException(
            status_code=400,
            detail="Manager's Google Drive not authorized. Manager needs to login again."
        )
    
    if not google_creds.get("refresh_token"):
        raise HTTPException(
            status_code=400,
            detail="Manager's Google credentials incomplete. Please re-authenticate."
        )
    
    # 初始化 Google Drive 服务
    try:
        drive_service = GoogleDriveService(
            access_token=google_creds["access_token"],
            refresh_token=google_creds.get("refresh_token")
        )
    except Exception as e:
        logger.error(f"Failed to initialize Drive service: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initialize Google Drive: {str(e)}")
    
    # 获取或创建项目文件夹
    project_folder_id = project.get("drive_folder_id")
    if not project_folder_id:
        logger.info("Creating project folder in Manager's Drive")
        try:
            project_name = project.get("name", f"Project_{project_id}")
            project_folder_id = drive_service.create_project_folder(f"OpenCoder_{project_name}")
            
            await core_db.projects.update_one(
                {"_id": project_oid},
                {"$set": {"drive_folder_id": project_folder_id}}
            )
            logger.info(f"Project folder created: {project_folder_id}")
        except Exception as e:
            logger.error(f"Failed to create project folder: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create project folder: {str(e)}")
    
    # 上传 PDF 到 Google Drive
    try:
        drive_result = drive_service.upload_file(
            file_content=file_content,
            filename=pdf.filename,
            mime_type="application/pdf",
            folder_id=project_folder_id
        )
        
        logger.info(f"PDF uploaded to Manager's Drive: {drive_result['drive_file_id']}")
        
    except Exception as e:
        logger.error(f"Failed to upload to Drive: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload to Google Drive: {str(e)}")
    
    # 如果 token 被刷新，更新数据库
    if drive_service.access_token != google_creds["access_token"]:
        logger.info("Manager's access token was refreshed")
        await core_db.users.update_one(
            {"_id": ObjectId(manager_id)},
            {
                "$set": {
                    "google_credentials.access_token": drive_service.access_token,
                    "google_credentials.token_expiry": datetime.utcnow() + timedelta(seconds=3600)
                }
            }
        )
    
    # 解析标签
    try:
        tags_list = json.loads(tags) if tags else []
    except Exception:
        tags_list = []
    
    # 获取项目数据库
    project_db = await get_project_db(project_id)
    
    # 生成可读的 Task ID
    today = datetime.utcnow()
    date_str = today.strftime("%Y%m%d")
    
    # 查找今天已有的 PDF 任务数量
    start_of_day = datetime(today.year, today.month, today.day)
    end_of_day = start_of_day + timedelta(days=1)
    
    count = await project_db.tasks.count_documents({
        "task_type": TaskType.PDF_DOCUMENT_CODING,
        "created_at": {"$gte": start_of_day, "$lt": end_of_day}
    })
    
    sequence = count + 1
    readable_task_id = f"PDF-{date_str}-{sequence:04d}"
    
    # 创建任务
    task_doc = {
        "title": title,
        "task_type": TaskType.PDF_DOCUMENT_CODING,
        "payload": {
            "text": None,
            "url": None,
            "pdf": {
                **drive_result,
                "page_count": None,  # Will be updated when PDF is loaded in frontend
                "uploaded_at": datetime.utcnow()
            },
            "meta": {
                "readable_task_id": readable_task_id,
                "original_filename": pdf.filename
            }
        },
        "status": "open",
        "tags": tags_list,
        "created_by": ObjectId(user_id),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    try:
        result = await project_db.tasks.insert_one(task_doc)
        task_id = result.inserted_id
        
        logger.info(f"Created PDF coding task: {task_id}")
        
        # 创建 PDF Document 记录
        pdf_doc = {
            "task_id": task_id,
            "file_name": pdf.filename,
            "drive_file_id": drive_result["drive_file_id"],
            "drive_file_url": drive_result["drive_file_url"],
            "mime_type": "application/pdf",
            "file_size": file_size,
            "page_count": None,
            "uploaded_by": ObjectId(user_id),
            # Store truncated extracted text for summary generation
            "extracted_text": extracted_text[:MAX_EXTRACT_CHARS] if extracted_text else None,
            "summary": None,
            "summary_model": None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        pdf_result = await project_db.pdf_documents.insert_one(pdf_doc)
        document_id = pdf_result.inserted_id
        
        logger.info(f"Created PDF document record: {document_id}")

        return {
            "success": True,
            "task_id": str(task_id),
            "document_id": str(document_id),
            "readable_task_id": readable_task_id,
            "pdf_url": drive_result["drive_file_url"],
            "message": "PDF Document Coding task created successfully",
            "has_text": bool(extracted_text.strip()),
        }
        
    except Exception as e:
        logger.error(f"Failed to create task: {e}")
        # Rollback: delete uploaded PDF
        try:
            drive_service.delete_file(drive_result["drive_file_id"])
            logger.info("Rolled back: deleted uploaded PDF")
        except Exception:
            pass
        
        raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")


@router.get("/{project_id}/pdf-coding/task/{task_id}")
async def get_pdf_coding_task(
    project_id: str,
    task_id: str,
    token: str = Query(...)
):
    """获取 PDF Coding 任务的完整信息"""
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    core_db = get_core_db()
    project_db = await get_project_db(project_id)
    
    try:
        task_oid = ObjectId(task_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid task ID")
    
    # 获取任务
    task = await project_db.tasks.find_one({"_id": task_oid})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    allowed_types = {
        TaskType.PDF_DOCUMENT_CODING,
        TaskType.PDF_DOCUMENT_CODING.value,
        TaskType.MULTIMODAL,
        TaskType.MULTIMODAL.value,
    }
    if task.get("task_type") not in allowed_types:
        raise HTTPException(status_code=400, detail="Not a PDF coding task")
    if task.get("task_type") in (TaskType.MULTIMODAL, TaskType.MULTIMODAL.value):
        if not (task.get("payload") or {}).get("pdf"):
            raise HTTPException(status_code=400, detail="Multimodal task has no PDF payload")
    
    # 获取 PDF Document
    document = await project_db.pdf_documents.find_one({"task_id": task_oid})
    if not document:
        raise HTTPException(status_code=404, detail="PDF document not found")
    
    # 获取 Document-level coding
    doc_coding = await project_db.document_level_coding.find_one({
        "task_id": task_oid,
        "coder_user_id": ObjectId(user_id)
    })
    
    # 获取 Passage annotations
    annotations = await project_db.passage_annotations.find({
        "task_id": task_oid,
        "coder_user_id": ObjectId(user_id)
    }).to_list(length=None)
    
    # 获取 Tag Groups (codes/codebook)
    tag_groups = await project_db.tag_groups.find({"active": True}).to_list(length=None)
    
    return {
        "task": {
            **task,
            "_id": str(task["_id"]),
            "created_by": str(task.get("created_by"))
        },
        "document": {
            "_id": str(document["_id"]),
            "task_id": str(document["task_id"]),
            "file_name": document.get("file_name"),
            "drive_file_id": document.get("drive_file_id"),
            "drive_file_url": document.get("drive_file_url"),
            "mime_type": document.get("mime_type", "application/pdf"),
            "file_size": document.get("file_size"),
            "page_count": document.get("page_count"),
            "uploaded_by": str(document.get("uploaded_by")),
            "summary": document.get("summary"),
            "summary_model": document.get("summary_model"),
        },
        "document_coding": {
            **doc_coding,
            "_id": str(doc_coding["_id"]),
            "task_id": str(doc_coding["task_id"]),
            "document_id": str(doc_coding["document_id"]),
            "code_ids": doc_coding.get("code_ids", []),  # Already strings
            "coder_user_id": str(doc_coding["coder_user_id"])
        } if doc_coding else None,
        "annotations": [
            {
                **ann,
                "_id": str(ann["_id"]),
                "task_id": str(ann["task_id"]),
                "document_id": str(ann["document_id"]),
                "code_ids": ann.get("code_ids", []),  # Already strings
                "coder_user_id": str(ann["coder_user_id"])
            }
            for ann in annotations
        ],
        "tag_groups": [
            {
                **tg,
                "_id": str(tg["_id"])
            }
            for tg in tag_groups
        ]
    }


@router.post("/{project_id}/pdf-coding/document-coding")
async def save_document_level_coding(
    project_id: str,
    data: DocumentLevelCodingCreate,
    token: str = Query(...)
):
    """保存或更新文档级别编码"""
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    project_db = await get_project_db(project_id)
    
    try:
        document_oid = ObjectId(data.document_id)
        # code_ids are option_ids (strings), NOT ObjectIds
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID format")
    
    # 获取 document 和 task
    document = await project_db.pdf_documents.find_one({"_id": document_oid})
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    task_id = document["task_id"]
    
    # 检查是否已有编码
    existing = await project_db.document_level_coding.find_one({
        "task_id": task_id,
        "document_id": document_oid,
        "coder_user_id": ObjectId(user_id)
    })
    
    now = datetime.utcnow()
    
    if existing:
        # 更新
        await project_db.document_level_coding.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "code_ids": data.code_ids,  # Keep as string array
                    "note": data.note,
                    "updated_at": now
                }
            }
        )
        return {"success": True, "message": "Document coding updated", "id": str(existing["_id"])}
    else:
        # 创建新的
        doc_coding = {
            "task_id": task_id,
            "document_id": document_oid,
            "code_ids": data.code_ids,  # Keep as string array
            "coder_user_id": ObjectId(user_id),
            "note": data.note,
            "created_at": now,
            "updated_at": now
        }
        
        result = await project_db.document_level_coding.insert_one(doc_coding)
        return {"success": True, "message": "Document coding created", "id": str(result.inserted_id)}


@router.post("/{project_id}/pdf-coding/passage-annotation")
async def create_passage_annotation(
    project_id: str,
    data: PassageAnnotationCreate,
    token: str = Query(...)
):
    """创建段落标注"""
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    project_db = await get_project_db(project_id)
    
    try:
        document_oid = ObjectId(data.document_id)
        # code_ids are option_ids (strings), NOT ObjectIds
        # They reference TagOption.option_id, not _id
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID format")
    
    # 获取 document
    document = await project_db.pdf_documents.find_one({"_id": document_oid})
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    task_id = document["task_id"]
    
    # 验证文本不为空
    if not data.selected_text or not data.selected_text.strip():
        raise HTTPException(status_code=400, detail="Selected text cannot be empty")
    
    # 创建标注 - code_ids保持为字符串数组（option_ids）
    now = datetime.utcnow()
    annotation = {
        "task_id": task_id,
        "document_id": document_oid,
        "page_number": data.page_number,
        "selected_text": data.selected_text,
        "start_offset": data.start_offset,
        "end_offset": data.end_offset,
        "rectangles": data.rectangles,
        "code_ids": data.code_ids,  # Keep as string array (option_ids)
        "coder_user_id": ObjectId(user_id),
        "note": data.note,
        "created_at": now,
        "updated_at": now
    }
    
    result = await project_db.passage_annotations.insert_one(annotation)
    
    return {
        "success": True,
        "message": "Passage annotation created",
        "id": str(result.inserted_id),
        "annotation": {
            **annotation,
            "_id": str(result.inserted_id),
            "task_id": str(task_id),
            "document_id": str(document_oid),
            "code_ids": annotation["code_ids"],  # Already strings
            "coder_user_id": str(annotation["coder_user_id"])
        }
    }


@router.put("/{project_id}/pdf-coding/passage-annotation/{annotation_id}")
async def update_passage_annotation(
    project_id: str,
    annotation_id: str,
    data: PassageAnnotationUpdate,
    token: str = Query(...)
):
    """更新段落标注"""
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    project_db = await get_project_db(project_id)
    
    try:
        annotation_oid = ObjectId(annotation_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid annotation ID")
    
    # 获取标注
    annotation = await project_db.passage_annotations.find_one({"_id": annotation_oid})
    if not annotation:
        raise HTTPException(status_code=404, detail="Annotation not found")
    
    # 验证权限
    if str(annotation["coder_user_id"]) != user_id:
        raise HTTPException(status_code=403, detail="Cannot modify other coder's annotation")
    
    # 构建更新
    update_data = {"updated_at": datetime.utcnow()}
    
    if data.selected_text is not None:
        update_data["selected_text"] = data.selected_text
    
    if data.code_ids is not None:
        # code_ids are option_ids (strings), not ObjectIds
        update_data["code_ids"] = data.code_ids
    
    if data.note is not None:
        update_data["note"] = data.note
    
    if data.rectangles is not None:
        update_data["rectangles"] = data.rectangles
    
    await project_db.passage_annotations.update_one(
        {"_id": annotation_oid},
        {"$set": update_data}
    )
    
    # 获取更新后的标注
    updated = await project_db.passage_annotations.find_one({"_id": annotation_oid})
    
    return {
        "success": True,
        "message": "Annotation updated",
        "annotation": {
            **updated,
            "_id": str(updated["_id"]),
            "task_id": str(updated["task_id"]),
            "document_id": str(updated["document_id"]),
            "code_ids": updated.get("code_ids", []),  # Already strings
            "coder_user_id": str(updated["coder_user_id"])
        }
    }


@router.delete("/{project_id}/pdf-coding/passage-annotation/{annotation_id}")
async def delete_passage_annotation(
    project_id: str,
    annotation_id: str,
    token: str = Query(...)
):
    """删除段落标注"""
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    project_db = await get_project_db(project_id)
    
    try:
        annotation_oid = ObjectId(annotation_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid annotation ID")
    
    # 获取标注
    annotation = await project_db.passage_annotations.find_one({"_id": annotation_oid})
    if not annotation:
        raise HTTPException(status_code=404, detail="Annotation not found")
    
    # 验证权限
    if str(annotation["coder_user_id"]) != user_id:
        raise HTTPException(status_code=403, detail="Cannot delete other coder's annotation")
    
    # 删除标注
    await project_db.passage_annotations.delete_one({"_id": annotation_oid})
    
    return {"success": True, "message": "Annotation deleted"}


@router.put("/{project_id}/pdf-coding/document/{document_id}/page-count")
async def update_document_page_count(
    project_id: str,
    document_id: str,
    page_count: int = Query(...),
    token: str = Query(...)
):
    """更新文档页数（从前端 PDF 加载后调用）"""
    try:
        user_data = verify_token(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    project_db = await get_project_db(project_id)
    
    try:
        document_oid = ObjectId(document_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID")
    
    # 更新页数
    result = await project_db.pdf_documents.update_one(
        {"_id": document_oid},
        {"$set": {"page_count": page_count, "updated_at": datetime.utcnow()}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # 同时更新 task payload 中的 page_count
    document = await project_db.pdf_documents.find_one({"_id": document_oid})
    if document:
        await project_db.tasks.update_one(
            {"_id": document["task_id"]},
            {"$set": {"payload.pdf.page_count": page_count, "updated_at": datetime.utcnow()}}
        )
    
    return {"success": True, "message": "Page count updated"}


# ── Generate / refresh summary for an existing PDF document ──────────────────

class PDFSummaryRequest(BaseModel):
    """Optional body: supply custom_text to bypass pypdf extraction (e.g. scanned PDFs)."""
    custom_text: Optional[str] = None


@router.post("/{project_id}/pdf-coding/document/{document_id}/summary")
async def generate_pdf_summary(
    project_id: str,
    document_id: str,
    token: str = Query(...),
    body: PDFSummaryRequest = PDFSummaryRequest(),
):
    """
    Generate (or regenerate) an AI summary for a PDF document.

    Text priority:
      1. body.custom_text  — user-supplied text (e.g. for scanned PDFs)
      2. doc.extracted_text — auto-extracted during upload
      3. re-download from Drive + pypdf extraction (fallback for old docs)

    If no text can be obtained at all, returns 422 with no_text=True so the
    frontend can show a manual-text input instead of a generic error.
    """
    try:
        verify_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        doc_oid = ObjectId(document_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID")

    project_db = await get_project_db(project_id)
    doc = await project_db.pdf_documents.find_one({"_id": doc_oid})
    if not doc:
        raise HTTPException(status_code=404, detail="PDF document not found")

    # ── 1. User-supplied text takes priority ──────────────────────────────────
    if body.custom_text and body.custom_text.strip():
        text = body.custom_text.strip()[:MAX_EXTRACT_CHARS]
        # Cache it so future calls don't need it again
        await project_db.pdf_documents.update_one(
            {"_id": doc_oid},
            {"$set": {"extracted_text": text, "updated_at": datetime.utcnow()}},
        )
    else:
        # ── 2. Already-stored extracted text ─────────────────────────────────
        text = doc.get("extracted_text") or ""

        # ── 3. Re-download from Drive and extract (old docs without stored text)
        if not text.strip():
            logger.info("No extracted_text for %s, re-downloading from Drive…", document_id)
            core_db = get_core_db()

            project_oid = ObjectId(project_id)
            project = await core_db.projects.find_one({"_id": project_oid})
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")

            manager_id = project.get("owner_user_id")
            manager = await core_db.users.find_one({"_id": manager_id}) if manager_id else None
            google_creds = (manager or {}).get("google_credentials", {})
            access_token = google_creds.get("access_token", "")
            refresh_token = google_creds.get("refresh_token")
            drive_file_id = doc.get("drive_file_id", "")

            if not drive_file_id:
                raise HTTPException(status_code=400, detail="Document has no Drive file ID")
            if not access_token:
                raise HTTPException(
                    status_code=400,
                    detail="Manager's Google Drive credentials not available. Please re-authenticate.",
                )

            import tempfile, os
            from services.transcription_service import download_drive_file
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp_path = tmp.name

                await download_drive_file(drive_file_id, access_token, refresh_token, tmp_path)

                with open(tmp_path, "rb") as f:
                    raw_bytes = f.read()

                logger.info("Downloaded %d bytes for doc %s (first 4: %s)",
                            len(raw_bytes), document_id, raw_bytes[:4])

                if not raw_bytes.startswith(b"%PDF"):
                    raise HTTPException(
                        status_code=500,
                        detail=f"Downloaded file does not look like a PDF (got {raw_bytes[:20]}). "
                               "The Drive link may have expired or returned an error page.",
                    )

                text = extract_pdf_text(raw_bytes)[:MAX_EXTRACT_CHARS]
                logger.info("Extracted %d chars from re-downloaded PDF for doc %s", len(text), document_id)

                if text.strip():
                    await project_db.pdf_documents.update_one(
                        {"_id": doc_oid},
                        {"$set": {"extracted_text": text, "updated_at": datetime.utcnow()}},
                    )
                else:
                    logger.warning("pypdf returned empty text for doc %s despite valid PDF bytes", document_id)

            except HTTPException:
                raise
            except Exception as e:
                logger.exception("Drive re-download failed for doc %s", document_id)
                raise HTTPException(status_code=500, detail=f"Failed to download PDF from Drive: {e}")
            finally:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

        # ── No text at all ────────────────────────────────────────────────────
        if not text.strip():
            raise HTTPException(
                status_code=422,
                detail={
                    "no_text": True,
                    "message": (
                        "pypdf could not extract any text from this PDF even though it is a valid PDF file. "
                        "This can happen with certain font encodings or copy-protected PDFs. "
                        "Please paste the document text manually below."
                    ),
                },
            )

    from services.transcription_service import generate_summary, SUMMARY_MODEL
    try:
        summary_text = await generate_summary(text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM summary failed: {e}")

    await project_db.pdf_documents.update_one(
        {"_id": doc_oid},
        {"$set": {
            "summary":       summary_text,
            "summary_model": SUMMARY_MODEL,
            "updated_at":    datetime.utcnow(),
        }},
    )

    return {"success": True, "summary": summary_text, "summary_model": SUMMARY_MODEL}
