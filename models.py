from pydantic import BaseModel, Field, EmailStr, ConfigDict
from typing import Optional, List, Dict, Any, Union
from datetime import datetime
from bson import ObjectId
from enum import Enum

# 自定义ObjectId类型
class PyObjectId(ObjectId):
    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        from pydantic_core import core_schema
        return core_schema.no_info_plain_validator_function(cls.validate)
    
    @classmethod
    def validate(cls, v):
        if isinstance(v, ObjectId):
            return v
        if isinstance(v, str):
            if ObjectId.is_valid(v):
                return ObjectId(v)
        raise ValueError("Invalid ObjectId")
    
    @classmethod
    def __get_pydantic_json_schema__(cls, field_schema, handler):
        return {"type": "string", "format": "objectid"}

# 枚举类型
class UserRole(str, Enum):
    MANAGER = "manager"
    CODER = "coder"

class ProjectStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"

class ApplicationStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

class TaskStatus(str, Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    DONE = "done"

class AssignmentState(str, Enum):
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    DONE = "done"

class TagGroupType(str, Enum):
    SINGLE = "single"
    MULTI = "multi"

class TaskType(str, Enum):
    TEXT = "text"
    URL = "url"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    BIBLIOGRAPHIC = "bibliographic"
    PDF_DOCUMENT_CODING = "pdf_document_coding"

# 基础模型
class BaseModelWithTimestamp(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

# 用户模型
class User(BaseModelWithTimestamp):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    email: EmailStr
    name: str
    avatar_url: Optional[str] = None
    role: Optional[UserRole] = None
    project_id: Optional[PyObjectId] = None
    use_external_mongo: bool = False
    external_mongo_uri_encrypted: Optional[str] = None

class UserCreate(BaseModel):
    email: EmailStr
    name: str
    avatar_url: Optional[str] = None

class UserUpdate(BaseModel):
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    role: Optional[UserRole] = None
    use_external_mongo: Optional[bool] = None
    external_mongo_uri_encrypted: Optional[str] = None

# 项目模型
class Project(BaseModelWithTimestamp):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    name: str
    slug: str
    owner_user_id: PyObjectId
    db_name: str
    cluster_uri: Optional[str] = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    tags: List[str] = []

class ProjectCreate(BaseModel):
    name: str
    slug: str
    tags: List[str] = []

# 申请模型
class Application(BaseModelWithTimestamp):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    project_id: PyObjectId
    applicant_user_id: PyObjectId
    applicant_name: Optional[str] = None  # ⭐ 冗余存储申请人姓名
    applicant_email: Optional[str] = None  # ⭐ 冗余存储申请人邮箱
    message: str
    status: ApplicationStatus = ApplicationStatus.PENDING
    project_name: Optional[str] = None  # 冗余存储项目名称（提高查询性能）
    project_slug: Optional[str] = None  # 冗余存储项目slug
    manager_email: Optional[str] = None  # 冗余存储 Manager 邮箱（便于联系）
    manager_name: Optional[str] = None  # 冗余存储 Manager 名字（便于显示）
    manager_user_id: Optional[PyObjectId] = None  # Manager 用户ID（可选）

class ApplicationCreate(BaseModel):
    message: str

class ApplicationUpdate(BaseModel):
    status: ApplicationStatus

# 任务模型
class ImageData(BaseModel):
    """图片数据结构"""
    drive_file_id: str
    drive_file_url: str
    drive_cdn_url: Optional[str] = None
    drive_view_url: Optional[str] = None
    drive_thumbnail_url: Optional[str] = None
    original_filename: str
    file_size: int
    mime_type: str
    uploaded_at: Optional[datetime] = None

class VideoData(BaseModel):
    """视频数据结构"""
    drive_file_id: str
    drive_file_url: str
    drive_download_url: Optional[str] = None
    drive_cdn_url: Optional[str] = None
    drive_view_url: Optional[str] = None
    drive_thumbnail_url: Optional[str] = None
    original_filename: str
    file_size: int
    mime_type: str
    uploaded_at: Optional[datetime] = None

class AudioData(BaseModel):
    """音频数据结构"""
    drive_file_id: str
    drive_file_url: str
    drive_download_url: Optional[str] = None
    drive_cdn_url: Optional[str] = None
    drive_view_url: Optional[str] = None
    drive_thumbnail_url: Optional[str] = None
    original_filename: str
    file_size: int
    mime_type: str
    uploaded_at: Optional[datetime] = None

class PDFData(BaseModel):
    """PDF文档数据结构"""
    drive_file_id: str
    drive_file_url: str
    drive_download_url: Optional[str] = None
    drive_view_url: Optional[str] = None
    original_filename: str
    file_size: int
    mime_type: str
    page_count: Optional[int] = None
    uploaded_at: Optional[datetime] = None

class TaskPayload(BaseModel):
    text: Optional[str] = None
    url: Optional[str] = None
    image: Optional[ImageData] = None
    video: Optional[VideoData] = None
    audio: Optional[AudioData] = None
    pdf: Optional[PDFData] = None
    meta: Dict[str, Any] = {}

class Task(BaseModelWithTimestamp):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    title: str
    task_type: TaskType = TaskType.TEXT
    payload: TaskPayload
    status: TaskStatus = TaskStatus.OPEN
    tags: List[str] = []
    created_by: PyObjectId

class TaskCreate(BaseModel):
    title: str
    task_type: Optional[TaskType] = None  # Optional, defaults to TEXT in Task model
    payload: TaskPayload
    tags: List[str] = []

class TaskBulkCreate(BaseModel):
    tasks: List[TaskCreate]

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    payload: Optional[TaskPayload] = None
    status: Optional[TaskStatus] = None
    tags: Optional[List[str]] = None

# 分配模型
class Assignment(BaseModelWithTimestamp):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    task_id: PyObjectId
    coder_user_id: PyObjectId
    state: AssignmentState = AssignmentState.ASSIGNED
    progress: int = Field(0, ge=0, le=100)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class AssignmentCreate(BaseModel):
    coder_user_id: PyObjectId
    task_ids: List[PyObjectId]
    state: Optional[AssignmentState] = AssignmentState.ASSIGNED

class AssignmentUpdate(BaseModel):
    state: Optional[AssignmentState] = None
    progress: Optional[int] = Field(None, ge=0, le=100)

# 标注模型
class LabelOption(BaseModel):
    group_id: str
    option_ids: List[str]

class Annotation(BaseModelWithTimestamp):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    task_id: PyObjectId
    coder_user_id: PyObjectId
    schema_version: int = 1
    labels: List[LabelOption] = []
    notes: Optional[str] = None
    completed_at: Optional[datetime] = None
    version: int = 1

class AnnotationCreate(BaseModel):
    task_id: PyObjectId
    labels: List[LabelOption]
    notes: Optional[str] = None

# 标签组模型
class TagOption(BaseModel):
    option_id: str
    label: str
    order: int
    active: bool = True

class TagGroupConstraints(BaseModel):
    mutex_with_groups: List[str] = []
    requires_groups: List[str] = []

class TagGroup(BaseModelWithTimestamp):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    group_id: str
    name: str
    description: str
    type: TagGroupType
    required: bool = False
    order: int
    active: bool = True
    options: List[TagOption] = []
    constraints: TagGroupConstraints = Field(default_factory=TagGroupConstraints)

class TagGroupCreate(BaseModel):
    group_id: str
    name: str
    description: str
    type: TagGroupType
    required: bool = False
    order: int
    active: bool = True
    options: List[TagOption] = []
    constraints: TagGroupConstraints = Field(default_factory=TagGroupConstraints)

class TagGroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    type: Optional[TagGroupType] = None
    required: Optional[bool] = None
    order: Optional[int] = None
    active: Optional[bool] = None
    options: Optional[List[TagOption]] = None
    constraints: Optional[TagGroupConstraints] = None

# 标签模式模型
class TagSchema(BaseModelWithTimestamp):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    schema_version: int
    effective_from: datetime
    groups: List[TagGroup] = []
    created_by: PyObjectId

# 项目元数据模型
class ProjectMeta(BaseModelWithTimestamp):
    id: str = Field(default="meta", alias="_id")
    tags: List[str] = []
    settings: Dict[str, Any] = {}
    audit: List[Dict[str, Any]] = []

# 响应模型
class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    limit: int
    pages: int

class BoardItem(BaseModel):
    task: Task
    assignments: List[Assignment]
    annotations: List[Annotation]

class BoardResponse(BaseModel):
    items: List[BoardItem]
    total: int

# ============== Chat Models ==============

class OnlineState(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    INVISIBLE = "invisible"

class ConversationType(str, Enum):
    GLOBAL = "global"
    PROJECT_GROUP = "project_group"
    P2P = "p2p"

class UserChatStatus(BaseModel):
    online_state: OnlineState = OnlineState.OFFLINE
    last_seen: datetime = Field(default_factory=datetime.utcnow)

class Conversation(BaseModelWithTimestamp):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    type: ConversationType
    participants: List[PyObjectId]
    project_id: Optional[PyObjectId] = None
    name: Optional[str] = None
    last_message: Optional[Dict[str, Any]] = None

class ChatMessage(BaseModelWithTimestamp):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    conversation_id: PyObjectId
    sender_id: PyObjectId
    content: str
    message_type: str = "text"
    read_by: List[PyObjectId] = []
    
class ChatMessageCreate(BaseModel):
    conversation_id: str
    content: str
    message_type: str = "text"

# ============== PDF Document Coding Models ==============

class PDFDocument(BaseModelWithTimestamp):
    """PDF文档模型"""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    task_id: PyObjectId
    file_name: str
    file_path: Optional[str] = None  # Optional local path if needed
    drive_file_id: str
    drive_file_url: str
    mime_type: str = "application/pdf"
    file_size: int
    page_count: Optional[int] = None
    uploaded_by: PyObjectId

class PDFDocumentCreate(BaseModel):
    task_id: str
    file_name: str
    drive_file_id: str
    drive_file_url: str
    file_size: int
    page_count: Optional[int] = None

class DocumentLevelCoding(BaseModelWithTimestamp):
    """文档级别编码"""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    task_id: PyObjectId
    document_id: PyObjectId
    code_ids: List[PyObjectId] = []  # Can have multiple codes
    coder_user_id: PyObjectId
    note: Optional[str] = None

class DocumentLevelCodingCreate(BaseModel):
    document_id: str
    code_ids: List[str]
    note: Optional[str] = None

class DocumentLevelCodingUpdate(BaseModel):
    code_ids: Optional[List[str]] = None
    note: Optional[str] = None

class RectangleCoordinate(BaseModel):
    """矩形坐标（标准化比例）"""
    x_ratio: float  # x / pageWidth
    y_ratio: float  # y / pageHeight
    width_ratio: float  # width / pageWidth
    height_ratio: float  # height / pageHeight
    page_width: float  # 原始页面宽度
    page_height: float  # 原始页面高度

class PassageAnnotation(BaseModelWithTimestamp):
    """段落级别标注"""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    task_id: PyObjectId
    document_id: PyObjectId
    page_number: int
    selected_text: str
    start_offset: Optional[int] = None  # Text start offset in page
    end_offset: Optional[int] = None  # Text end offset in page
    rectangles: List[RectangleCoordinate] = []  # Support multi-line selection
    code_ids: List[PyObjectId] = []  # Can have multiple codes
    coder_user_id: PyObjectId
    note: Optional[str] = None

class PassageAnnotationCreate(BaseModel):
    document_id: str
    page_number: int
    selected_text: str
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    rectangles: List[Dict[str, float]] = []
    code_ids: List[str] = []
    note: Optional[str] = None

class PassageAnnotationUpdate(BaseModel):
    selected_text: Optional[str] = None
    code_ids: Optional[List[str]] = None
    note: Optional[str] = None
    rectangles: Optional[List[Dict[str, float]]] = None