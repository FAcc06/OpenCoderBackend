"""
音频/视频代理路由
用于代理 Google Drive 文件以解决 CORS 限制
"""
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
import httpx
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/proxy/drive/{file_id}")
async def proxy_drive_file(file_id: str):
    """
    代理 Google Drive 文件，解决 CORS 限制
    
    Args:
        file_id: Google Drive 文件 ID
        
    Returns:
        文件内容流
    """
    try:
        # 构建 Google Drive 下载 URL
        download_url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t"
        
        logger.info(f"Proxying file: {file_id}")
        
        # 使用 httpx 获取文件
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            response = await client.get(download_url)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch file {file_id}: {response.status_code}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Failed to fetch file from Google Drive: {response.status_code}"
                )
            
            # 获取内容类型
            content_type = response.headers.get('content-type', 'application/octet-stream')
            
            # 返回流式响应
            return StreamingResponse(
                iter([response.content]),
                media_type=content_type,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, OPTIONS",
                    "Access-Control-Allow-Headers": "*",
                    "Cache-Control": "public, max-age=3600",
                }
            )
            
    except httpx.HTTPError as e:
        logger.error(f"HTTP error while proxying file {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to proxy file: {str(e)}")
    except Exception as e:
        logger.error(f"Error proxying file {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}")

@router.options("/proxy/drive/{file_id}")
async def proxy_drive_file_options(file_id: str):
    """处理 CORS 预检请求"""
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )
