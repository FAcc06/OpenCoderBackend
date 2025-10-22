from typing import List, Dict, Any
from bson import ObjectId
from cryptography.fernet import Fernet
import os
from dotenv import load_dotenv

load_dotenv()

def encrypt_data(data: str) -> str:
    """加密数据"""
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise ValueError("ENCRYPTION_KEY not found")
    
    # 确保密钥长度为32字节
    key = key[:32].ljust(32, '0')
    f = Fernet(key.encode())
    return f.encrypt(data.encode()).decode()

def decrypt_data(encrypted_data: str) -> str:
    """解密数据"""
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise ValueError("ENCRYPTION_KEY not found")
    
    # 确保密钥长度为32字节
    key = key[:32].ljust(32, '0')
    f = Fernet(key.encode())
    return f.decrypt(encrypted_data.encode()).decode()

def validate_object_id(id_str: str) -> ObjectId:
    """验证并转换ObjectId"""
    try:
        return ObjectId(id_str)
    except Exception:
        raise ValueError(f"Invalid ObjectId: {id_str}")

def paginate_query(query, page: int = 1, limit: int = 10):
    """分页查询"""
    skip = (page - 1) * limit
    return query.skip(skip).limit(limit)

def calculate_pages(total: int, limit: int) -> int:
    """计算总页数"""
    return (total + limit - 1) // limit

def validate_tag_group_constraints(labels: List[Dict[str, Any]], tag_groups: List[Dict[str, Any]]):
    """验证标签组约束"""
    if not tag_groups:
        # 如果没有标签组定义，跳过验证
        return
    
    group_dict = {group["group_id"]: group for group in tag_groups}
    
    # 检查所有必填的标签组是否都有提供
    required_groups = {group["group_id"] for group in tag_groups if group.get("required", False)}
    provided_groups = {label["group_id"] for label in labels}
    missing_groups = required_groups - provided_groups
    
    if missing_groups:
        raise ValueError(f"Required tag groups missing: {', '.join(missing_groups)}")
    
    for label in labels:
        group_id = label.get("group_id")
        option_ids = label.get("option_ids", [])
        
        if not group_id:
            raise ValueError("Label missing group_id")
        
        if group_id not in group_dict:
            raise ValueError(f"Tag group '{group_id}' not found")
        
        group = group_dict[group_id]
        
        # 检查必选标签组是否有选择
        if group.get("required", False) and not option_ids:
            raise ValueError(f"Tag group '{group_id}' is required but no options selected")
        
        # 检查单选约束
        if group.get("type") == "single" and len(option_ids) > 1:
            raise ValueError(f"Tag group '{group_id}' only allows single selection, but {len(option_ids)} options provided")
        
        # 检查选项是否有效
        # 同时支持 option_id 和 label（兼容性处理）
        options = group.get("options", [])
        valid_option_ids = {opt["option_id"] for opt in options if opt.get("active", True)}
        option_id_to_label = {opt["option_id"]: opt.get("label") for opt in options if opt.get("active", True)}
        label_to_option_id = {opt.get("label"): opt["option_id"] for opt in options if opt.get("active", True)}
        
        for i, option_id in enumerate(option_ids):
            # 如果是 label，自动转换为 option_id
            if option_id in label_to_option_id:
                option_ids[i] = label_to_option_id[option_id]
                option_id = option_ids[i]
            
            # 验证 option_id 是否有效
            if option_id not in valid_option_ids:
                raise ValueError(f"Invalid option '{option_id}' for group '{group_id}'")

def generate_db_name(project_slug: str) -> str:
    """生成项目数据库名称"""
    return f"proj_{project_slug.lower().replace('-', '_')}"

def sanitize_slug(slug: str) -> str:
    """清理slug格式"""
    return slug.lower().replace(' ', '-').replace('_', '-')

def create_audit_entry(action: str, who: str, detail: Dict[str, Any] = None) -> Dict[str, Any]:
    """创建审计条目"""
    return {
        "at": datetime.utcnow(),
        "who": who,
        "action": action,
        "detail": detail or {}
    }

from datetime import datetime



