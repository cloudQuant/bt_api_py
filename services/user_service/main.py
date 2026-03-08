"""
用户服务 - 微服务架构实现
支持百万级并发用户的用户管理
"""

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional, List
import asyncio
import json
import hashlib
import jwt
from datetime import datetime, timedelta
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from .database import get_async_session
from .models import User, UserProfile, UserConfig
from .cache import get_redis_client
from .auth import create_access_token, verify_token
from .metrics import metrics_collector


class UserCreateRequest(BaseModel):
    """用户创建请求"""

    email: EmailStr
    username: str
    password: str
    full_name: Optional[str] = None
    phone: Optional[str] = None


class UserResponse(BaseModel):
    """用户响应"""

    user_id: str
    email: str
    username: str
    full_name: Optional[str]
    is_verified: bool
    created_at: datetime
    updated_at: datetime


class UserProfileUpdate(BaseModel):
    """用户资料更新请求"""

    full_name: Optional[str] = None
    phone: Optional[str] = None
    avatar_url: Optional[str] = None
    timezone: Optional[str] = None
    language: Optional[str] = "zh-CN"


class UserService:
    """用户服务 - 支持百万级并发"""

    def __init__(self):
        self.app = FastAPI(
            title="User Service", description="bt_api_py 用户管理微服务", version="2.0.0"
        )
        self.setup_routes()
        self.setup_middleware()

    def setup_middleware(self):
        """设置中间件"""

        @self.app.middleware("http")
        async def add_process_time_header(request, call_next):
            start_time = asyncio.get_event_loop().time()
            response = await call_next(request)
            process_time = asyncio.get_event_loop().time() - start_time
            response.headers["X-Process-Time"] = str(process_time)

            # 记录指标
            metrics_collector.record_request(
                method=request.method,
                endpoint=request.url.path,
                status=response.status_code,
                duration=process_time,
            )

            return response

    def setup_routes(self):
        """设置路由"""

        @self.app.post("/api/v1/users/register", response_model=UserResponse)
        async def register_user(user_data: UserCreateRequest):
            """用户注册 - 支持高并发"""
            with metrics_collector.request_latency.time():
                return await self._register_user_internal(user_data)

        @self.app.post("/api/v1/users/authenticate")
        async def authenticate_user(credentials: dict):
            """用户认证 - JWT + OAuth2.0"""
            with metrics_collector.request_latency.time():
                return await self._authenticate_user_internal(credentials)

        @self.app.get("/api/v1/users/profile", response_model=UserResponse)
        async def get_user_profile(current_user: dict = Depends(verify_token)):
            """获取用户信息 - 缓存优化"""
            with metrics_collector.request_latency.time():
                return await self._get_user_profile_internal(current_user["user_id"])

        @self.app.put("/api/v1/users/profile")
        async def update_user_profile(
            profile_data: UserProfileUpdate, current_user: dict = Depends(verify_token)
        ):
            """更新用户资料"""
            with metrics_collector.request_latency.time():
                return await self._update_user_profile_internal(
                    current_user["user_id"], profile_data
                )

        @self.app.post("/api/v1/users/refresh-token")
        async def refresh_token(current_user: dict = Depends(verify_token)):
            """刷新访问令牌"""
            with metrics_collector.request_latency.time():
                return await self._refresh_token_internal(current_user["user_id"])

        @self.app.delete("/api/v1/users/logout")
        async def logout_user(current_user: dict = Depends(verify_token)):
            """用户登出 - 撤销令牌"""
            with metrics_collector.request_latency.time():
                return await self._logout_user_internal(current_user["user_id"])

        @self.app.get("/health")
        async def health_check():
            """健康检查"""
            return {"status": "healthy", "timestamp": datetime.utcnow()}

        @self.app.get("/ready")
        async def readiness_check():
            """就绪检查"""
            # 检查数据库连接
            try:
                async with get_async_session() as session:
                    await session.execute(select(1).limit(1))
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not ready"
                )

            # 检查Redis连接
            try:
                redis_client = await get_redis_client()
                await redis_client.ping()
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Cache not ready"
                )

            return {"status": "ready", "timestamp": datetime.utcnow()}

    async def _register_user_internal(self, user_data: UserCreateRequest) -> UserResponse:
        """内部用户注册实现"""
        # 并行执行验证和数据库操作
        async with get_async_session() as session:
            # 检查邮箱和用户名是否已存在
            existing_user = await session.execute(
                select(User).where(
                    (User.email == user_data.email) | (User.username == user_data.username)
                )
            )
            if existing_user.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="Email or username already exists"
                )

            # 创建新用户
            password_hash = self._hash_password(user_data.password)
            new_user = User(
                email=user_data.email,
                username=user_data.username,
                password_hash=password_hash,
                full_name=user_data.full_name,
                phone=user_data.phone,
                is_verified=False,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )

            session.add(new_user)
            await session.commit()
            await session.refresh(new_user)

            # 创建用户配置
            user_config = UserConfig(
                user_id=new_user.user_id,
                timezone="UTC",
                language="zh-CN",
                trading_enabled=True,
                api_key_limit=100,
                created_at=datetime.utcnow(),
            )
            session.add(user_config)
            await session.commit()

            # 缓存用户信息
            redis_client = await get_redis_client()
            user_cache_key = f"user:{new_user.user_id}"
            await redis_client.setex(
                user_cache_key,
                3600,  # 1小时缓存
                json.dumps(
                    {
                        "user_id": new_user.user_id,
                        "email": new_user.email,
                        "username": new_user.username,
                        "full_name": new_user.full_name,
                        "is_verified": new_user.is_verified,
                    },
                    default=str,
                ),
            )

            # 发布用户注册事件
            await self._publish_user_event(
                "user_registered",
                {
                    "user_id": new_user.user_id,
                    "email": new_user.email,
                    "username": new_user.username,
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )

            return UserResponse(
                user_id=new_user.user_id,
                email=new_user.email,
                username=new_user.username,
                full_name=new_user.full_name,
                is_verified=new_user.is_verified,
                created_at=new_user.created_at,
                updated_at=new_user.updated_at,
            )

    async def _authenticate_user_internal(self, credentials: dict) -> dict:
        """内部用户认证实现"""
        email = credentials.get("email")
        password = credentials.get("password")

        if not email or not password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Email and password are required"
            )

        async with get_async_session() as session:
            # 查询用户
            user = await session.execute(select(User).where(User.email == email))
            user = user.scalar_one_or_none()

            if not user or not self._verify_password(password, user.password_hash):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
                )

            # 更新最后登录时间
            user.last_login_at = datetime.utcnow()
            await session.commit()

            # 生成JWT令牌
            access_token = create_access_token(
                data={"user_id": user.user_id, "email": user.email},
                expires_delta=timedelta(hours=24),
            )

            # 缓存令牌
            redis_client = await get_redis_client()
            token_key = f"token:{access_token}"
            await redis_client.setex(
                token_key,
                86400,  # 24小时
                json.dumps({"user_id": user.user_id}, default=str),
            )

            # 发布登录事件
            await self._publish_user_event(
                "user_logged_in",
                {
                    "user_id": user.user_id,
                    "email": user.email,
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )

            return {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": 86400,
                "user": {
                    "user_id": user.user_id,
                    "email": user.email,
                    "username": user.username,
                    "full_name": user.full_name,
                },
            }

    async def _get_user_profile_internal(self, user_id: str) -> UserResponse:
        """内部获取用户资料实现"""
        # 尝试从缓存获取
        redis_client = await get_redis_client()
        cache_key = f"user:{user_id}"
        cached_user = await redis_client.get(cache_key)

        if cached_user:
            user_data = json.loads(cached_user)
            return UserResponse(**user_data)

        # 从数据库获取
        async with get_async_session() as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            user = user.scalar_one_or_none()

            if not user:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

            user_response = UserResponse(
                user_id=user.user_id,
                email=user.email,
                username=user.username,
                full_name=user.full_name,
                is_verified=user.is_verified,
                created_at=user.created_at,
                updated_at=user.updated_at,
            )

            # 缓存结果
            await redis_client.setex(
                cache_key,
                1800,  # 30分钟缓存
                json.dumps(user_response.dict(), default=str),
            )

            return user_response

    async def _update_user_profile_internal(
        self, user_id: str, profile_data: UserProfileUpdate
    ) -> dict:
        """内部更新用户资料实现"""
        async with get_async_session() as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            user = user.scalar_one_or_none()

            if not user:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

            # 更新字段
            update_data = profile_data.dict(exclude_unset=True)
            for field, value in update_data.items():
                setattr(user, field, value)

            user.updated_at = datetime.utcnow()
            await session.commit()

            # 清除缓存
            redis_client = await get_redis_client()
            cache_key = f"user:{user_id}"
            await redis_client.delete(cache_key)

            # 发布更新事件
            await self._publish_user_event(
                "user_profile_updated",
                {
                    "user_id": user_id,
                    "updated_fields": list(update_data.keys()),
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )

            return {"message": "Profile updated successfully"}

    async def _refresh_token_internal(self, user_id: str) -> dict:
        """内部刷新令牌实现"""
        async with get_async_session() as session:
            user = await session.execute(select(User).where(User.user_id == user_id))
            user = user.scalar_one_or_none()

            if not user:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

            # 生成新令牌
            access_token = create_access_token(
                data={"user_id": user.user_id, "email": user.email},
                expires_delta=timedelta(hours=24),
            )

            # 缓存新令牌
            redis_client = await get_redis_client()
            token_key = f"token:{access_token}"
            await redis_client.setex(
                token_key, 86400, json.dumps({"user_id": user.user_id}, default=str)
            )

            return {"access_token": access_token, "token_type": "bearer", "expires_in": 86400}

    async def _logout_user_internal(self, user_id: str) -> dict:
        """内部用户登出实现"""
        # 删除所有用户令牌
        redis_client = await get_redis_client()
        pattern = f"token:*"
        keys = await redis_client.keys(pattern)

        if keys:
            # 批量检查和删除
            pipe = redis_client.pipeline()
            for key in keys:
                token_data = await redis_client.get(key)
                if token_data:
                    data = json.loads(token_data)
                    if data.get("user_id") == user_id:
                        pipe.delete(key)
            await pipe.execute()

        # 清除用户缓存
        cache_key = f"user:{user_id}"
        await redis_client.delete(cache_key)

        # 发布登出事件
        await self._publish_user_event(
            "user_logged_out", {"user_id": user_id, "timestamp": datetime.utcnow().isoformat()}
        )

        return {"message": "Logged out successfully"}

    def _hash_password(self, password: str) -> str:
        """密码哈希"""
        import bcrypt

        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    def _verify_password(self, password: str, hashed: str) -> bool:
        """验证密码"""
        import bcrypt

        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))

    async def _publish_user_event(self, event_type: str, event_data: dict):
        """发布用户事件"""
        try:
            # 这里应该连接到Kafka或其他消息队列
            # 简化实现，实际应该使用异步消息客户端
            print(f"Publishing {event_type}: {event_data}")

            # 示例Kafka发布
            # from kafka import KafkaProducer
            # producer = KafkaProducer(
            #     bootstrap_servers=['kafka:9092'],
            #     value_serializer=lambda v: json.dumps(v).encode('utf-8')
            # )
            # await producer.send('user_events', {
            #     'type': event_type,
            #     'data': event_data,
            #     'timestamp': datetime.utcnow().isoformat()
            # })

        except Exception as e:
            # 事件发布失败不应该影响主要功能
            print(f"Failed to publish user event: {e}")


# 创建应用实例
user_service = UserService()
app = user_service.app

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
