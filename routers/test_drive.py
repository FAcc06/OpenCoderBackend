"""
Google Drive 测试端点
用于验证 Drive API 集成是否正常工作
"""
from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from bson import ObjectId
from datetime import datetime, timedelta
import logging

from database import get_core_db
from routers.auth import verify_token
from services.google_drive import GoogleDriveService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/test/drive/status")
async def test_drive_status(token: str = Query(...)):
    """
    测试用户的 Google Drive 授权状态
    
    返回：
    - 是否已授权
    - access_token 是否存在
    - refresh_token 是否存在
    - 授权的 scopes
    """
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
        
        core_db = get_core_db()
        user = await core_db.users.find_one({"_id": ObjectId(user_id)})
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        google_creds = user.get("google_credentials")
        
        if not google_creds:
            return {
                "success": False,
                "authorized": False,
                "message": "Google Drive not authorized. Please re-login to grant Drive access.",
                "has_access_token": False,
                "has_refresh_token": False,
                "scopes": []
            }
        
        has_drive_scope = any('drive' in scope for scope in google_creds.get('scopes', []))
        
        return {
            "success": True,
            "authorized": True,
            "has_access_token": bool(google_creds.get("access_token")),
            "has_refresh_token": bool(google_creds.get("refresh_token")),
            "scopes": google_creds.get("scopes", []),
            "has_drive_scope": has_drive_scope,
            "token_expiry": google_creds.get("token_expiry"),
            "message": "Google Drive authorized" if has_drive_scope else "Drive scope not granted"
        }
        
    except Exception as e:
        logger.error(f"Error checking Drive status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/test/drive/upload")
async def test_drive_upload(
    token: str = Query(...),
    file: UploadFile = File(...),
    folder_name: str = Form("OpenCoder_Test")
):
    """
    测试上传文件到 Google Drive
    
    这是一个测试端点，用于验证 Drive API 是否正常工作
    """
    try:
        # 验证用户
        user_data = verify_token(token)
        user_id = user_data.get("sub")
        
        # 获取用户 credentials
        core_db = get_core_db()
        user = await core_db.users.find_one({"_id": ObjectId(user_id)})
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        google_creds = user.get("google_credentials")
        if not google_creds:
            raise HTTPException(
                status_code=400, 
                detail="Google Drive not authorized. Please re-login."
            )
        
        if not google_creds.get("access_token"):
            raise HTTPException(status_code=400, detail="No access token available")
        
        # 验证文件类型
        if not file.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="Only image files are allowed")
        
        # 读取文件内容
        file_content = await file.read()
        file_size = len(file_content)
        
        # 限制文件大小 (10MB)
        if file_size > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image size exceeds 10MB limit")
        
        logger.info(f"Uploading test file: {file.filename} ({file_size} bytes)")
        
        # 初始化 Drive 服务
        drive_service = GoogleDriveService(
            access_token=google_creds["access_token"],
            refresh_token=google_creds.get("refresh_token")
        )
        
        # 创建测试文件夹（如果需要）
        # folder_id = drive_service.create_project_folder(folder_name)
        
        # 上传图片
        result = drive_service.upload_image(
            file_content=file_content,
            filename=file.filename,
            mime_type=file.content_type,
            folder_id=None  # 测试时上传到根目录
        )
        
        # 如果 token 刷新了，更新数据库
        if drive_service.credentials.token != google_creds["access_token"]:
            logger.info("Token was refreshed, updating database")
            await core_db.users.update_one(
                {"_id": ObjectId(user_id)},
                {
                    "$set": {
                        "google_credentials.access_token": drive_service.credentials.token,
                        "google_credentials.token_expiry": datetime.utcnow() + timedelta(seconds=3600)
                    }
                }
            )
        
        return {
            "success": True,
            "message": "Image uploaded successfully to Google Drive",
            "file_info": result,
            "test_url": result["drive_file_url"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload test failed: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.delete("/api/test/drive/delete/{file_id}")
async def test_drive_delete(
    file_id: str,
    token: str = Query(...)
):
    """
    测试删除 Google Drive 文件
    """
    try:
        # 验证用户
        user_data = verify_token(token)
        user_id = user_data.get("sub")
        
        # 获取用户 credentials
        core_db = get_core_db()
        user = await core_db.users.find_one({"_id": ObjectId(user_id)})
        
        if not user or not user.get("google_credentials"):
            raise HTTPException(status_code=400, detail="Google Drive not authorized")
        
        google_creds = user["google_credentials"]
        
        # 初始化 Drive 服务
        drive_service = GoogleDriveService(
            access_token=google_creds["access_token"],
            refresh_token=google_creds.get("refresh_token")
        )
        
        # 删除文件
        success = drive_service.delete_file(file_id)
        
        if success:
            return {
                "success": True,
                "message": f"File {file_id} deleted successfully"
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to delete file")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete test failed: {e}")
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")
