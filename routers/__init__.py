# 路由模块初始化文件
# 导入所有路由模块使其可以通过 `from routers import xxx` 访问

from . import (
    users,
    projects,
    applications,
    tasks,
    assignments,
    annotations,
    tag_groups,
    board,
    public,
    dashboard,
    auth,
    llm,
    notifications,
    chat,
    test_drive,
    exports,
    consensus
)

__all__ = [
    'users',
    'projects',
    'applications',
    'tasks',
    'assignments',
    'annotations',
    'tag_groups',
    'board',
    'public',
    'dashboard',
    'auth',
    'llm',
    'notifications',
    'chat',
    'test_drive',
    'exports',
    'consensus'
]
