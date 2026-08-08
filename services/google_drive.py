"""
Google Drive 服务
用于上传、管理和删除文件（图片、视频、音频等）
"""
import os
import io
import json
from typing import Optional, Dict, Any, List
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.auth.transport.requests import Request
import httpx
import logging

logger = logging.getLogger(__name__)


class GoogleDriveService:
    """Google Drive 文件管理服务"""
    
    def __init__(self, access_token: str, refresh_token: Optional[str] = None):
        """
        初始化 Google Drive 服务
        
        Args:
            access_token: Google OAuth access token
            refresh_token: Google OAuth refresh token（用于自动刷新）
        """
        # 如果有 refresh_token，创建完整凭证（可自动刷新）
        if refresh_token:
            self.credentials = Credentials(
                token=access_token,
                refresh_token=refresh_token,
                token_uri='https://oauth2.googleapis.com/token',
                client_id=os.getenv('GOOGLE_CLIENT_ID'),
                client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
                scopes=['https://www.googleapis.com/auth/drive.file']
            )
            
            # 如果 token 过期，自动刷新
            if self.credentials.expired and self.credentials.refresh_token:
                logger.info("Access token expired, refreshing...")
                self.credentials.refresh(Request())
                logger.info("Access token refreshed successfully")
        else:
            # 没有 refresh_token 时，使用简单凭证（无法自动刷新）
            logger.warning("No refresh_token provided. Token cannot be auto-refreshed.")
            self.credentials = Credentials(
                token=access_token,
                scopes=['https://www.googleapis.com/auth/drive.file']
            )
        
        self.service = build('drive', 'v3', credentials=self.credentials)

    @property
    def access_token(self) -> str:
        """获取当前有效的 access token（必要时自动刷新）"""
        if self.credentials.expired and self.credentials.refresh_token:
            self.credentials.refresh(Request())
        return self.credentials.token
    
    def create_project_folder(self, project_name: str) -> str:
        """
        创建项目专用文件夹
        
        Args:
            project_name: 项目名称
            
        Returns:
            folder_id: 创建的文件夹 ID
        """
        folder_metadata = {
            'name': f'OpenCoder_Project_{project_name}',
            'mimeType': 'application/vnd.google-apps.folder'
        }
        
        try:
            folder = self.service.files().create(
                body=folder_metadata,
                fields='id, name'
            ).execute()
            
            folder_id = folder.get('id')
            logger.info(f"Created project folder: {folder.get('name')} (ID: {folder_id})")
            return folder_id
            
        except Exception as e:
            logger.error(f"Failed to create folder: {e}")
            raise Exception(f"Failed to create Drive folder: {str(e)}")
    
    def upload_file(
        self, 
        file_content: bytes, 
        filename: str, 
        mime_type: str,
        folder_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        上传文件到 Google Drive（支持图片、视频、音频等所有文件类型）
        
        Args:
            file_content: 文件的二进制内容
            filename: 文件名
            mime_type: MIME 类型 (image/*, video/*, audio/*, 等)
            folder_id: 目标文件夹 ID（可选）
            
        Returns:
            dict: 包含文件信息的字典
        """
        file_metadata = {
            'name': filename,
        }
        
        # 如果指定了文件夹，添加到 parents
        if folder_id:
            file_metadata['parents'] = [folder_id]
        
        # 创建可上传的媒体对象
        media = MediaIoBaseUpload(
            io.BytesIO(file_content),
            mimetype=mime_type,
            resumable=True
        )
        
        try:
            # 上传文件
            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name, webViewLink, webContentLink, size, thumbnailLink'
            ).execute()
            
            file_id = file.get('id')
            logger.info(f"Uploaded file: {filename} (ID: {file_id})")
            
            # 设置文件为公开可查看（任何人有链接就能查看）
            permission = {
                'type': 'anyone',
                'role': 'reader'
            }
            self.service.permissions().create(
                fileId=file_id,
                body=permission,
                fields='id'
            ).execute()
            
            logger.info(f"Set file {file_id} to public (anyone with link)")
            
            # 重新获取文件信息以获取公开后的 webContentLink
            file_updated = self.service.files().get(
                fileId=file_id,
                fields='id, name, size, webViewLink, webContentLink, thumbnailLink'
            ).execute()
            
            web_content_link = file_updated.get('webContentLink', '')
            logger.info(f"File webContentLink: {web_content_link}")
            
            # 返回文件信息
            # 使用适合网页显示的 URL 格式
            # Google Drive 文件显示 URL：
            # 1. uc?export=view&id=FILE_ID - 直接显示/播放（主要）
            # 2. lh3.googleusercontent.com/d/FILE_ID - Google CDN（备用，图片更快）
            # 3. uc?export=download&id=FILE_ID - 下载链接（视频/音频备用）
            display_url = f"https://drive.google.com/uc?export=view&id={file_id}"
            download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
            cdn_url = f"https://lh3.googleusercontent.com/d/{file_id}"  # CDN 备选（主要用于图片）
            
            return {
                "drive_file_id": file_id,
                "drive_file_url": display_url,  # 主要显示/播放URL
                "drive_download_url": download_url,  # 下载URL（视频/音频备用）
                "drive_cdn_url": cdn_url,  # CDN备用URL（图片更快）
                "drive_view_url": file_updated.get('webViewLink', f"https://drive.google.com/file/d/{file_id}/view"),
                "drive_thumbnail_url": file_updated.get('thumbnailLink', ''),
                "original_filename": filename,
                "file_size": int(file_updated.get('size', 0)),
                "mime_type": mime_type
            }
            
        except Exception as e:
            logger.error(f"Failed to upload file: {e}")
            raise Exception(f"Failed to upload to Drive: {str(e)}")
    
    # 保持向后兼容：upload_image 是 upload_file 的别名
    upload_image = upload_file

    def create_resumable_upload_session(
        self,
        filename: str,
        mime_type: str,
        file_size: int,
        folder_id: Optional[str] = None,
        origin: Optional[str] = None,
    ) -> str:
        """
        创建 Google Drive resumable 上传会话，返回供浏览器直传的 upload URL。
        必须传入 Origin，浏览器才能跨域 PUT 到该 URL。
        """
        metadata: Dict[str, Any] = {"name": filename}
        if folder_id:
            metadata["parents"] = [folder_id]

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": mime_type,
            "X-Upload-Content-Length": str(file_size),
        }
        if origin:
            headers["Origin"] = origin

        url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,name,size,mimeType"

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, headers=headers, content=json.dumps(metadata))

            if response.status_code not in (200, 201):
                logger.error(
                    "Failed to start resumable session: %s %s",
                    response.status_code,
                    response.text,
                )
                raise Exception(
                    f"Failed to start resumable upload session: {response.status_code} {response.text}"
                )

            upload_url = response.headers.get("Location")
            if not upload_url:
                raise Exception("Resumable upload session missing Location header")

            logger.info("Created resumable upload session for %s", filename)
            return upload_url
        except Exception as e:
            logger.error("Failed to create resumable session: %s", e)
            raise Exception(f"Failed to create resumable upload session: {str(e)}")

    def _build_file_result(
        self,
        file_id: str,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
        file_size: Optional[int] = None,
        file_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build the standard drive_* URL payload used by task documents."""
        meta = file_meta or {}
        resolved_name = filename or meta.get("name") or "file"
        resolved_mime = mime_type or meta.get("mimeType") or "application/octet-stream"
        resolved_size = file_size
        if resolved_size is None:
            resolved_size = int(meta.get("size", 0) or 0)

        display_url = f"https://drive.google.com/uc?export=view&id={file_id}"
        download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        cdn_url = f"https://lh3.googleusercontent.com/d/{file_id}"

        return {
            "drive_file_id": file_id,
            "drive_file_url": display_url,
            "drive_download_url": download_url,
            "drive_cdn_url": cdn_url,
            "drive_view_url": meta.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view"),
            "drive_thumbnail_url": meta.get("thumbnailLink", ""),
            "original_filename": resolved_name,
            "file_size": resolved_size,
            "mime_type": resolved_mime,
        }

    def file_in_folder(self, file_id: str, folder_id: str) -> bool:
        """Return True if file_id has folder_id among its parents."""
        try:
            file = self.service.files().get(
                fileId=file_id,
                fields="id,parents",
            ).execute()
            parents = file.get("parents") or []
            return folder_id in parents
        except Exception as e:
            logger.error("Failed to check file folder for %s: %s", file_id, e)
            return False

    def finalize_uploaded_file(
        self,
        file_id: str,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
        file_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        客户端直传完成后：设为 anyone-with-link 可读，并返回标准 URL 字段。
        """
        try:
            permission = {"type": "anyone", "role": "reader"}
            self.service.permissions().create(
                fileId=file_id,
                body=permission,
                fields="id",
            ).execute()
            logger.info("Set file %s to public (anyone with link)", file_id)

            file_updated = self.service.files().get(
                fileId=file_id,
                fields="id, name, size, mimeType, webViewLink, webContentLink, thumbnailLink",
            ).execute()

            return self._build_file_result(
                file_id=file_id,
                filename=filename,
                mime_type=mime_type,
                file_size=file_size,
                file_meta=file_updated,
            )
        except Exception as e:
            logger.error("Failed to finalize uploaded file %s: %s", file_id, e)
            raise Exception(f"Failed to finalize uploaded file: {str(e)}")
    
    def delete_file(self, file_id: str) -> bool:
        """
        删除 Google Drive 文件
        
        Args:
            file_id: 文件 ID
            
        Returns:
            bool: 是否删除成功
        """
        try:
            self.service.files().delete(fileId=file_id).execute()
            logger.info(f"Deleted file: {file_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete file {file_id}: {e}")
            return False
    
    def get_file_info(self, file_id: str) -> Dict[str, Any]:
        """
        获取文件信息
        
        Args:
            file_id: 文件 ID
            
        Returns:
            dict: 文件信息
        """
        try:
            file = self.service.files().get(
                fileId=file_id,
                fields='id, name, mimeType, size, webViewLink, webContentLink, thumbnailLink'
            ).execute()
            
            return {
                "drive_file_id": file.get('id'),
                "drive_file_url": f"https://drive.google.com/uc?export=view&id={file.get('id')}",
                "drive_thumbnail_url": file.get('thumbnailLink'),
                "original_filename": file.get('name'),
                "file_size": int(file.get('size', 0)),
                "mime_type": file.get('mimeType')
            }
            
        except Exception as e:
            logger.error(f"Failed to get file info {file_id}: {e}")
            raise Exception(f"Failed to get file info: {str(e)}")
    
    def share_with_emails(self, file_id: str, email_list: List[str], role: str = 'reader') -> Dict[str, Any]:
        """
        批量分享文件给指定邮箱列表
        
        Args:
            file_id: Google Drive 文件 ID
            email_list: 邮箱列表
            role: 权限角色 ('reader', 'writer', 'commenter')
        
        Returns:
            分享结果统计
        """
        success_count = 0
        failed_emails = []
        
        for email in email_list:
            try:
                permission = {
                    'type': 'user',
                    'role': role,
                    'emailAddress': email
                }
                
                self.service.permissions().create(
                    fileId=file_id,
                    body=permission,
                    sendNotificationEmail=False
                ).execute()
                
                success_count += 1
                logger.info(f"✅ Shared file {file_id} with {email}")
                
            except Exception as e:
                logger.warning(f"⚠️ Failed to share with {email}: {str(e)}")
                failed_emails.append(email)
        
        return {
            'success_count': success_count,
            'failed_count': len(failed_emails),
            'failed_emails': failed_emails
        }
