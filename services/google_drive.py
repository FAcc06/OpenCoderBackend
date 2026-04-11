"""
Google Drive 服务
用于上传、管理和删除图片文件
"""
import os
import io
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.auth.transport.requests import Request
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
        """获取当前 access token"""
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
    
    def upload_image(
        self, 
        file_content: bytes, 
        filename: str, 
        mime_type: str,
        folder_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        上传图片到 Google Drive
        
        Args:
            file_content: 图片文件的二进制内容
            filename: 文件名
            mime_type: MIME 类型 (image/jpeg, image/png 等)
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
            # Google Drive 图片显示 URL 的最佳格式：
            # 1. uc?export=view&id=FILE_ID - 直接显示图片（主要）
            # 2. lh3.googleusercontent.com/d/FILE_ID - Google CDN（备用，更快）
            display_url = f"https://drive.google.com/uc?export=view&id={file_id}"
            cdn_url = f"https://lh3.googleusercontent.com/d/{file_id}"  # CDN 备选
            
            return {
                "drive_file_id": file_id,
                "drive_file_url": display_url,  # 主要显示URL
                "drive_cdn_url": cdn_url,  # CDN备用URL（更快）
                "drive_view_url": file_updated.get('webViewLink', f"https://drive.google.com/file/d/{file_id}/view"),
                "drive_thumbnail_url": file_updated.get('thumbnailLink', ''),
                "original_filename": filename,
                "file_size": int(file_updated.get('size', 0)),
                "mime_type": mime_type
            }
            
        except Exception as e:
            logger.error(f"Failed to upload file: {e}")
            raise Exception(f"Failed to upload to Drive: {str(e)}")
    
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
    
    @property
    def access_token(self) -> str:
        """获取当前有效的 access token"""
        if self.credentials.expired and self.credentials.refresh_token:
            self.credentials.refresh(Request())
        return self.credentials.token
