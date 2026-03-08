# bt_api_py 分布式系统架构重构

## 执行摘要

本文档描述了 bt_api_py 从单体架构向分布式微服务架构的完整重构方案，目标是支持百万级并发用户和73+交易所的高性能交易系统。

## 架构演进路线图

### 当前架构痛点
- **单体瓶颈**：500行BtApi类承载过多职责
- **扩展性限制**：无法水平扩展，单点故障风险
- **部署复杂**：73个交易所耦合部署，发布风险高
- **团队协作**：代码冲突频繁，开发效率低

### 目标架构
```
┌─────────────────────────────────────────────────────────────┐
│                   API Gateway (Kong/Envoy)                │
├─────────────────────────────────────────────────────────────┤
│                 Service Mesh (Istio)                      │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │User Svc  │ │Order Svc │ │Market Svc│ │Risk Svc │ │
│  └──────────┘ └──────────┘ └──────────┘ └─────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │Auth Svc  │ │Data Svc  │ │Notify Svc│ │Admin Svc│ │
│  └──────────┘ └──────────┘ └──────────┘ └─────────┘ │
├─────────────────────────────────────────────────────────────┤
│                 Message Bus (Kafka)                       │
├─────────────────────────────────────────────────────────────┤
│    Redis Cluster    │    PostgreSQL + TimescaleDB      │
└─────────────────────────────────────────────────────────────┘
```

## 微服务拆分设计

### 1. 用户服务 (User Service)
**职责**：用户注册、认证、权限管理、配置信息
```python
# user_service/main.py
from fastapi import FastAPI
from pydantic import BaseModel
import asyncio

class UserService:
    def __init__(self):
        self.app = FastAPI(title="User Service")
        self.setup_routes()
    
    async def register_user(self, user_data: dict) -> dict:
        """用户注册 - 支持百万级并发"""
        # 异步数据库操作
        # 事件发布到Kafka
        # 缓存用户信息到Redis
        pass
    
    async def authenticate_user(self, credentials: dict) -> dict:
        """用户认证 - JWT + OAuth2.0"""
        pass
    
    async def get_user_profile(self, user_id: str) -> dict:
        """获取用户信息 - 缓存优化"""
        pass

app = UserService().app
```

### 2. 订单服务 (Order Service)
**职责**：订单创建、执行、状态管理、历史查询
```python
# order_service/main.py
from fastapi import FastAPI
from aiohttp import ClientSession
import asyncio

class OrderService:
    def __init__(self):
        self.app = FastAPI(title="Order Service")
        self.exchange_clients = {}
        self.setup_routes()
    
    async def create_order(self, order_request: dict) -> dict:
        """创建订单 - 路由到对应交易所"""
        exchange_name = order_request['exchange_name']
        client = self.exchange_clients.get(exchange_name)
        
        if not client:
            raise ValueError(f"Exchange {exchange_name} not supported")
        
        # 异步执行订单
        order_id = await client.submit_order(order_request)
        
        # 发布事件
        await self.publish_order_event({
            'action': 'created',
            'order_id': order_id,
            'exchange': exchange_name,
            'timestamp': datetime.utcnow()
        })
        
        return {'order_id': order_id, 'status': 'submitted'}
    
    async def cancel_order(self, order_id: str, exchange_name: str) -> dict:
        """取消订单 - 支持批量取消"""
        pass
    
    async def get_order_status(self, order_id: str) -> dict:
        """查询订单状态 - 实时更新"""
        pass

app = OrderService().app
```

### 3. 行情服务 (Market Service)
**职责**：实时行情数据、K线数据、技术指标计算
```python
# market_service/main.py
from fastapi import FastAPI, WebSocket
from asyncio import Queue
import websockets

class MarketService:
    def __init__(self):
        self.app = FastAPI(title="Market Service")
        self.websocket_connections = set()
        self.data_queue = Queue(maxsize=10000)
        self.setup_routes()
    
    async def get_ticker(self, exchange: str, symbol: str) -> dict:
        """获取行情 - 缓存优化，<1ms响应"""
        cache_key = f"ticker:{exchange}:{symbol}"
        
        # 尝试从Redis缓存获取
        cached = await self.redis.get(cache_key)
        if cached:
            return json.loads(cached)
        
        # 从交易所获取
        ticker = await self.get_exchange_ticker(exchange, symbol)
        
        # 缓存结果（TTL: 1秒）
        await self.redis.setex(cache_key, 1, json.dumps(ticker))
        
        return ticker
    
    async def subscribe_ticker(self, websocket: WebSocket, exchange: str, symbol: str):
        """WebSocket订阅 - 支持百万级连接"""
        await websocket.accept()
        self.websocket_connections.add(websocket)
        
        try:
            while True:
                # 推送实时数据
                ticker = await self.get_ticker(exchange, symbol)
                await websocket.send_json(ticker)
                await asyncio.sleep(0.1)  # 100ms更新频率
        except WebSocketDisconnect:
            self.websocket_connections.discard(websocket)
    
    async def broadcast_ticker_updates(self):
        """广播行情更新 - 批量处理优化"""
        while True:
            batch = []
            for _ in range(100):  # 批量处理100个消息
                try:
                    message = await asyncio.wait_for(
                        self.data_queue.get(), timeout=0.01
                    )
                    batch.append(message)
                except asyncio.TimeoutError:
                    break
            
            if batch:
                # 广播给所有连接
                disconnected = set()
                for ws in self.websocket_connections:
                    try:
                        await ws.send_json({'data': batch})
                    except WebSocketDisconnect:
                        disconnected.add(ws)
                
                # 清理断开的连接
                self.websocket_connections -= disconnected

app = MarketService().app
```

### 4. 风控服务 (Risk Service)
**职责**：风险计算、限制检查、异常检测、合规验证
```python
# risk_service/main.py
from fastapi import FastAPI
import numpy as np
from typing import Dict, List

class RiskService:
    def __init__(self):
        self.app = FastAPI(title="Risk Service")
        self.risk_models = {}
        self.setup_routes()
    
    async def check_order_risk(self, order_request: dict) -> dict:
        """订单风险检查 - 实时计算，<10ms响应"""
        user_id = order_request['user_id']
        symbol = order_request['symbol']
        volume = order_request['volume']
        
        # 并行执行多个风险检查
        checks = await asyncio.gather(
            self.check_position_limit(user_id, symbol, volume),
            self.check_daily_volume_limit(user_id, volume),
            self.check_market_impact(symbol, volume),
            self.check_compliance(order_request)
        )
        
        # 汇总风险结果
        risk_score = sum(check['score'] for check in checks)
        allowed = risk_score < 0.8  # 风险阈值
        
        return {
            'allowed': allowed,
            'risk_score': risk_score,
            'checks': checks,
            'reason': 'High risk detected' if not allowed else None
        }
    
    async def check_position_limit(self, user_id: str, symbol: str, volume: float) -> dict:
        """持仓限制检查"""
        # 获取当前持仓
        positions = await self.get_user_positions(user_id, symbol)
        current_volume = sum(pos['volume'] for pos in positions)
        
        # 获取用户限制
        limits = await self.get_user_limits(user_id)
        max_position = limits.get('max_position_per_symbol', 1000000)
        
        if current_volume + volume > max_position:
            return {'score': 0.9, 'reason': 'Position limit exceeded'}
        
        return {'score': 0.1, 'reason': 'Position limit OK'}
    
    async def check_market_impact(self, symbol: str, volume: float) -> dict:
        """市场冲击检查 - 使用订单簿数据"""
        orderbook = await self.get_orderbook(symbol)
        
        # 计算市场冲击
        impact = self.calculate_market_impact(orderbook, volume)
        
        if impact > 0.05:  # 5%价格冲击阈值
            return {'score': 0.8, 'reason': 'High market impact'}
        
        return {'score': impact * 10, 'reason': f'Market impact: {impact:.2%}'}

app = RiskService().app
```

## 服务通信架构

### API网关配置
```yaml
# kong.yml
services:
  - name: user-service
    url: http://user-service:8000
    routes:
      - name: user-routes
        paths: ["/api/v1/users"]
  
  - name: order-service
    url: http://order-service:8000
    routes:
      - name: order-routes
        paths: ["/api/v1/orders"]

  - name: market-service
    url: http://market-service:8000
    routes:
      - name: market-routes
        paths: ["/api/v1/market"]

plugins:
  - name: rate-limiting
    service: user-service
    config:
      minute: 1000
      hour: 10000
  
  - name: prometheus
    service: all
    config:
      per_consumer: true
```

### 服务网格配置 (Istio)
```yaml
# istio-gateway.yaml
apiVersion: networking.istio.io/v1beta1
kind: Gateway
metadata:
  name: btapi-gateway
spec:
  selector:
    istio: ingressgateway
  servers:
    - port:
        number: 80
        name: http
      hosts:
        - api.btapi.com
---
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: btapi-vs
spec:
  hosts:
    - api.btapi.com
  gateways:
    - btapi-gateway
  http:
    - match:
        - uri:
            prefix: /api/v1/users
      route:
        - destination:
            host: user-service
            port:
              number: 8000
    - match:
        - uri:
            prefix: /api/v1/orders
      route:
        - destination:
            host: order-service
            port:
              number: 8000
```

## 数据层设计

### 分布式缓存策略
```python
# cache_manager.py
import redis.asyncio as redis
import json
from typing import Optional, Any

class DistributedCache:
    def __init__(self):
        self.redis_cluster = redis.RedisCluster(
            startup_nodes=[
                {"host": "redis-node-1", "port": 7000},
                {"host": "redis-node-2", "port": 7000},
                {"host": "redis-node-3", "port": 7000},
            ],
            decode_responses=True
        )
    
    async def get(self, key: str) -> Optional[Any]:
        """获取缓存 - 支持分布式锁"""
        try:
            value = await self.redis_cluster.get(key)
            return json.loads(value) if value else None
        except Exception as e:
            logger.error(f"Cache get error: {e}")
            return None
    
    async def set(self, key: str, value: Any, ttl: int = 300):
        """设置缓存 - 自动过期"""
        try:
            await self.redis_cluster.setex(
                key, ttl, json.dumps(value, default=str)
            )
        except Exception as e:
            logger.error(f"Cache set error: {e}")
    
    async def get_distributed_lock(self, lock_key: str, ttl: int = 10) -> bool:
        """分布式锁 - 防止并发问题"""
        result = await self.redis_cluster.set(
            lock_key, "locked", ex=ttl, nx=True
        )
        return result is not None
```

### 数据库分片策略
```python
# database_manager.py
import asyncpg
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker

class DatabaseManager:
    def __init__(self):
        self.engines = {}
        self.setup_shards()
    
    def setup_shards(self):
        """设置数据库分片 - 按用户ID分片"""
        shard_configs = [
            {'host': 'db-shard-1', 'database': 'btapi_shard_1'},
            {'host': 'db-shard-2', 'database': 'btapi_shard_2'},
            {'host': 'db-shard-3', 'database': 'btapi_shard_3'},
            {'host': 'db-shard-4', 'database': 'btapi_shard_4'},
        ]
        
        for i, config in enumerate(shard_configs):
            engine = create_async_engine(
                f"postgresql+asyncpg://user:pass@{config['host']}/{config['database']}",
                pool_size=20,
                max_overflow=30
            )
            self.engines[i] = engine
    
    def get_shard(self, user_id: str) -> int:
        """根据用户ID计算分片"""
        hash_value = hash(user_id) % 4
        return abs(hash_value)
    
    async def execute_query(self, user_id: str, query: str, params: dict = None):
        """在对应分片执行查询"""
        shard_id = self.get_shard(user_id)
        engine = self.engines[shard_id]
        
        async with engine.connect() as conn:
            result = await conn.execute(text(query), params or {})
            return result.fetchall()
```

## 部署配置

### Kubernetes部署清单
```yaml
# user-service-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: user-service
  labels:
    app: user-service
spec:
  replicas: 10
  selector:
    matchLabels:
      app: user-service
  template:
    metadata:
      labels:
        app: user-service
    spec:
      containers:
      - name: user-service
        image: btapi/user-service:latest
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: btapi-secrets
              key: database-url
        - name: REDIS_URL
          value: "redis://redis-cluster:6379"
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: user-service
spec:
  selector:
    app: user-service
  ports:
  - port: 8000
    targetPort: 8000
  type: ClusterIP
```

### 自动扩缩容配置
```yaml
# user-service-hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: user-service-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: user-service
  minReplicas: 5
  maxReplicas: 100
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
  - type: Pods
    pods:
      metric:
        name: http_requests_per_second
      target:
        type: AverageValue
        averageValue: "100"
```

## 性能优化策略

### 连接池优化
```python
# connection_pool.py
import asyncio
from aiohttp import ClientSession, TCPConnector

class OptimizedConnectionPool:
    def __init__(self):
        self.connector = TCPConnector(
            limit=100,           # 总连接数限制
            limit_per_host=20,    # 每个主机连接数限制
            keepalive_timeout=30,  # 保持连接时间
            enable_cleanup_closed=True,
            use_dns_cache=True,
        )
        self.session = ClientSession(connector=self.connector)
    
    async def request(self, method: str, url: str, **kwargs):
        """优化的HTTP请求 - 连接复用"""
        # 添加重试逻辑
        for attempt in range(3):
            try:
                async with self.session.request(method, url, **kwargs) as response:
                    return await response.json()
            except Exception as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)  # 指数退避
```

### 批处理优化
```python
# batch_processor.py
import asyncio
from typing import List, Dict, Any

class BatchProcessor:
    def __init__(self, batch_size: int = 100, flush_interval: float = 1.0):
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.pending_items = []
        self.last_flush = asyncio.get_event_loop().time()
        self._flush_task = None
    
    async def add_item(self, item: Dict[str, Any]):
        """添加项目到批处理队列"""
        self.pending_items.append(item)
        
        if (len(self.pending_items) >= self.batch_size or 
            asyncio.get_event_loop().time() - self.last_flush >= self.flush_interval):
            await self.flush()
    
    async def flush(self):
        """刷新批处理队列"""
        if not self.pending_items:
            return
        
        batch = self.pending_items.copy()
        self.pending_items.clear()
        self.last_flush = asyncio.get_event_loop().time()
        
        # 并行处理批处理项目
        await asyncio.gather(*[
            self.process_item(item) for item in batch
        ])
    
    async def process_item(self, item: Dict[str, Any]):
        """处理单个项目"""
        # 具体的处理逻辑
        pass
```

## 监控和可观测性

### Prometheus指标配置
```python
# metrics.py
from prometheus_client import Counter, Histogram, Gauge, start_http_server

# 定义指标
REQUEST_COUNT = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
REQUEST_DURATION = Histogram('http_request_duration_seconds', 'HTTP request duration')
ACTIVE_CONNECTIONS = Gauge('active_connections', 'Number of active connections')
ORDER_LATENCY = Histogram('order_latency_seconds', 'Order processing latency')
RISK_SCORES = Histogram('risk_check_score', 'Risk check scores')

class MetricsCollector:
    @staticmethod
    def record_request(method: str, endpoint: str, status: int, duration: float):
        REQUEST_COUNT.labels(method=method, endpoint=endpoint, status=status).inc()
        REQUEST_DURATION.observe(duration)
    
    @staticmethod
    def record_order_latency(latency: float):
        ORDER_LATENCY.observe(latency)
    
    @staticmethod
    def update_active_connections(count: int):
        ACTIVE_CONNECTIONS.set(count)
```

## 迁移策略

### 蓝绿部署策略
```python
# deployment.py
import asyncio
from kubernetes import client, config

class BlueGreenDeployment:
    def __init__(self):
        config.load_incluster_config()
        self.k8s_apps = client.AppsV1Api()
        self.k8s_core = client.CoreV1Api()
    
    async def deploy_blue_green(self, service_name: str, new_image: str):
        """蓝绿部署 - 零停机时间"""
        # 1. 创建绿色环境
        green_deployment = self.create_deployment(
            name=f"{service_name}-green",
            image=new_image
        )
        
        # 2. 等待绿色环境就绪
        await self.wait_for_deployment_ready(f"{service_name}-green")
        
        # 3. 切换流量到绿色环境
        await self.switch_traffic(service_name, "green")
        
        # 4. 验证绿色环境
        if await self.verify_deployment(service_name):
            # 5. 清理蓝色环境
            await self.cleanup_deployment(f"{service_name}-blue")
        else:
            # 6. 回滚到蓝色环境
            await self.switch_traffic(service_name, "blue")
            await self.cleanup_deployment(f"{service_name}-green")
            raise Exception("Green deployment failed, rolled back")
```

## 测试策略

### 混沌工程测试
```python
# chaos_testing.py
import random
import asyncio
from typing import Dict, List

class ChaosEngine:
    def __init__(self):
        self.injected_faults = []
    
    async def inject_latency(self, service_name: str, latency_ms: int):
        """注入延迟故障"""
        print(f"Injecting {latency_ms}ms latency into {service_name}")
        # 实际实现需要service mesh支持
        pass
    
    async def inject_error(self, service_name: str, error_rate: float):
        """注入错误故障"""
        print(f"Injecting {error_rate:.1%} error rate into {service_name}")
        pass
    
    async def inject_network_partition(self, services: List[str]):
        """注入网络分区"""
        print(f"Injecting network partition between {services}")
        pass
    
    async def run_chaos_experiment(self, experiment_config: Dict):
        """运行混沌实验"""
        for fault in experiment_config['faults']:
            if fault['type'] == 'latency':
                await self.inject_latency(fault['service'], fault['duration'])
            elif fault['type'] == 'error':
                await self.inject_error(fault['service'], fault['rate'])
            elif fault['type'] == 'partition':
                await self.inject_network_partition(fault['services'])
```

## 总结

这个分布式系统架构重构将 bt_api_py 从单体应用升级为企业级微服务平台，具备：

- **扩展性**：支持百万级并发用户
- **可靠性**：99.99%可用性，故障隔离
- **性能**：亚秒级响应时间，高吞吐量
- **可维护性**：服务独立部署和扩展
- **可观测性**：全面的监控和追踪

通过分阶段实施，可以在保持现有服务的同时逐步升级架构，最终实现世界级的交易基础设施平台。