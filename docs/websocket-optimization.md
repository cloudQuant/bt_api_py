# Advanced WebSocket Optimization for bt_api_py

This document describes the comprehensive WebSocket optimization system implemented for bt_api_py, providing production-grade connection management, error handling, and performance monitoring for 73+ exchanges.

## 🚀 Features

### Advanced Connection Management
- **Intelligent Connection Pooling**: Dynamic connection pooling with health monitoring
- **Exponential Backoff Reconnection**: Smart reconnection with configurable strategies
- **Circuit Breaker Pattern**: Automatic fault detection and recovery
- **Load Balancing**: Round-robin, least-connections, weighted, and random strategies
- **Failover Support**: Multiple endpoint support with automatic failover

### Robust Error Handling
- **Categorized Error Types**: Network, authentication, rate limiting, protocol, exchange-specific
- **Recovery Strategies**: Automatic retry with exponential backoff
- **Dead Letter Queue**: Failed message handling with retry logic
- **Circuit Breaker**: Prevents cascade failures

### Performance Optimization
- **Message Batching**: Efficient message processing and compression
- **Backpressure Handling**: Flow control and queue management
- **Memory Management**: Connection lifecycle management and cleanup
- **Rate Limiting**: Exchange-specific rate limiting compliance

### Monitoring & Observability
- **Real-time Metrics**: Latency, throughput, error rates, connection health
- **Performance Alerts**: Configurable alerting with multiple severity levels
- **Benchmarking**: Automated performance testing and reporting
- **Dashboard Integration**: Comprehensive monitoring dashboard

## 📁 Architecture

```
bt_api_py/websocket/
├── advanced_connection_manager.py    # Core WebSocket connection management
├── advanced_websocket_manager.py     # Pool management and load balancing
├── exchange_adapters.py            # Exchange-specific implementations
├── monitoring.py                   # Metrics, alerts, and benchmarking
└── __init__.py                    # Unified API integration
```

## 🔧 Usage

### Basic Usage

```python
import asyncio
from bt_api_py.websocket import (
    subscribe_to_ticker,
    subscribe_to_depth,
    get_websocket_stats
)

async def handle_ticker(data):
    print(f"Ticker: {data['symbol']} = ${data['last_price']}")

# Subscribe to BTC ticker on Binance
subscription_id = await subscribe_to_ticker(
    "BINANCE", "BTCUSDT", handle_ticker
)

# Get statistics
stats = await get_websocket_stats()
print(f"Active connections: {stats['global_metrics']['active_connections']}")
```

### Advanced Usage

```python
from bt_api_py.websocket import (
    WebSocketConfig,
    PoolConfiguration,
    ExchangeCredentials,
    get_websocket_manager
)

# Create custom configuration
config = WebSocketConfig(
    url="wss://stream.binance.com:9443",
    exchange_name="BINANCE",
    max_connections=10,
    heartbeat_interval=30.0,
    reconnect_enabled=True,
    compression=True
)

pool_config = PoolConfiguration(
    min_connections=2,
    max_connections=8,
    load_balance_strategy="weighted",
    failover_enabled=True
)

# Setup with authentication
credentials = ExchangeCredentials(
    exchange_name="BINANCE",
    auth_type=AuthenticationType.API_KEY_SECRET,
    api_key="your_key",
    api_secret="your_secret"
)

# Get manager and add exchange
manager = await get_websocket_manager()
await manager.add_exchange(config, pool_config)
```

### Monitoring and Alerts

```python
from bt_api_py.websocket import (
    get_websocket_monitor,
    PerformanceAlert,
    AlertSeverity
)

async def custom_alert_handler(alert):
    print(f"Alert: {alert.name} - {alert.description}")

monitor = await get_websocket_monitor()

# Add custom alert
alert = PerformanceAlert(
    alert_id="custom_latency",
    name="High Latency Alert",
    severity=AlertSeverity.WARNING,
    condition="avg_latency_ms",
    threshold=500.0,
    description="Average latency exceeds 500ms"
)

monitor.alert_manager.add_alert(alert)
monitor.alert_manager.add_notification_handler(custom_alert_handler)
```

## 🔌 Exchange Support

### Supported Exchanges
- **Binance**: Spot, Futures, Options with full authentication
- **OKX**: Spot, Futures with custom authentication
- **Generic**: Template for additional exchanges

### Adding New Exchanges

```python
from bt_api_py.websocket.exchange_adapters import ExchangeWebSocketAdapter

class NewExchangeAdapter(ExchangeWebSocketAdapter):
    async def authenticate(self, websocket):
        # Exchange-specific authentication
        pass
    
    def format_subscription_message(self, subscription_id, topic, symbol, params):
        # Format for this exchange
        return {"action": "subscribe", "symbol": symbol, "topic": topic}
    
    def extract_topic_symbol(self, message):
        # Parse incoming messages
        return message.get("topic"), message.get("symbol")

# Register adapter
from bt_api_py.websocket.exchange_adapters import WebSocketAdapterFactory
WebSocketAdapterFactory.register_adapter("NEW_EXCHANGE", NewExchangeAdapter)
```

## 📊 Performance Features

### Connection Health Monitoring
- **Connection States**: Disconnected, Connecting, Connected, Authenticated, Error
- **Health Scoring**: Dynamic health assessment based on multiple metrics
- **Automatic Recovery**: Self-healing connections with backoff strategies

### Metrics Collection
- **Latency Tracking**: Average, P95, P99 percentiles
- **Error Rate Monitoring**: Per-category error tracking
- **Throughput Analysis**: Messages per second, byte rates
- **Resource Usage**: Memory and CPU monitoring

### Benchmarking
- **Latency Benchmarks**: Round-trip time measurement
- **Throughput Tests**: Message processing capacity
- **Memory Profiling**: Usage patterns and leak detection

## ⚡ Performance Optimizations

### Connection Pooling
- **Dynamic Scaling**: Automatic connection count adjustment
- **Health-based Routing**: Route to healthiest connections
- **Idle Connection Cleanup**: Resource management

### Message Processing
- **Batch Operations**: Group messages for efficiency
- **Compression**: Automatic message compression
- **Backpressure Control**: Prevent memory overflow

### Rate Limiting
- **Exchange-specific Limits**: Respects exchange rate limits
- **Token Bucket Algorithm**: Fair request distribution
- **Adaptive Throttling**: Dynamic limit adjustment

## 🛡️ Error Handling & Recovery

### Circuit Breaker
- **Failure Detection**: Automatic pattern recognition
- **Adaptive Thresholds**: Self-adjusting limits
- **Graceful Degradation**: Maintain partial functionality

### Dead Letter Queue
- **Failed Message Storage**: Preserves important data
- **Retry Logic**: Configurable retry strategies
- **Alerting**: Notification on persistent failures

### Connection Recovery
- **Exponential Backoff**: Smart retry timing
- **Endpoint Failover**: Multiple endpoint support
- **State Preservation**: Maintain subscription state

## 📈 Monitoring Dashboard

### Real-time Statistics
```python
from bt_api_py.websocket import get_monitoring_dashboard

dashboard = await get_monitoring_dashboard()
print(f"Active alerts: {dashboard['active_alerts']}")
print(f"Latency avg: {dashboard['metrics_summary']['websocket_latency']['mean']}ms")
print(f"Error rate: {dashboard['metrics_summary']['websocket_errors']['mean']}/min")
```

### Alert Management
- **Severity Levels**: Info, Warning, Error, Critical
- **Custom Conditions**: Flexible alert configuration
- **Notification Channels**: Multiple handler support

## 🧪 Testing

### Running Tests
```bash
# Basic tests
pytest tests/websocket/test_advanced_websocket.py -v

# Integration tests
pytest tests/websocket/ -m integration -v

# Performance tests
pytest tests/websocket/ -m benchmark -v
```

### Test Coverage
- **Unit Tests**: Individual component testing
- **Integration Tests**: End-to-end workflow testing
- **Performance Tests**: Load and stress testing
- **Mock Tests**: Exchange adapter testing

## 🔧 Configuration

### Connection Configuration
```python
config = WebSocketConfig(
    # Basic connection
    url="wss://stream.exchange.com",
    exchange_name="EXCHANGE",
    endpoints=["backup1.exchange.com", "backup2.exchange.com"],
    
    # Pooling
    max_connections=5,
    min_connections=1,
    connection_timeout=30.0,
    idle_timeout=300.0,
    
    # Reconnection
    reconnect_enabled=True,
    reconnect_interval=1.0,
    max_reconnect_attempts=10,
    reconnect_backoff_multiplier=2.0,
    max_reconnect_delay=60.0,
    
    # Performance
    compression=True,
    message_buffer_size=8192,
    send_buffer_size=1024,
    receive_buffer_size=8192,
    
    # Rate limiting
    rate_limit_enabled=True,
    max_requests_per_second=10,
    max_subscriptions_per_connection=50,
    
    # Subscription limits
    subscription_limits={
        "ticker": 100,
        "depth": 50,
        "trades": 100,
        "kline": 200
    }
)
```

### Pool Configuration
```python
pool_config = PoolConfiguration(
    # Pool sizing
    min_connections=2,
    max_connections=10,
    connection_timeout=30.0,
    
    # Load balancing
    load_balance_strategy="round_robin",  # round_robin, least_connections, random, weighted
    health_check_interval=30.0,
    connection_max_age=3600.0,
    
    # Failover
    failover_enabled=True,
    failover_threshold=0.5,
    failover_timeout=60.0,
    
    # Performance
    message_batching=True,
    batch_size=10,
    batch_timeout=0.1,
    
    # Monitoring
    metrics_enabled=True,
    detailed_logging=False
)
```

## 🚀 Best Practices

### Production Deployment
1. **Use connection pooling** for high-throughput applications
2. **Configure appropriate timeouts** based on network conditions
3. **Enable compression** for bandwidth efficiency
4. **Set up monitoring** for proactive issue detection
5. **Configure rate limits** to respect exchange policies

### Performance Tuning
1. **Monitor latency** and adjust connection count accordingly
2. **Use appropriate load balancing** strategy
3. **Optimize subscription limits** per exchange
4. **Enable health checks** for early failure detection
5. **Configure alerts** for critical metrics

### Error Handling
1. **Implement retry logic** with exponential backoff
2. **Use circuit breakers** for fault tolerance
3. **Log errors appropriately** for debugging
4. **Set up dead letter queues** for failed messages
5. **Monitor error rates** and adjust accordingly

## 🐛 Troubleshooting

### Common Issues
1. **Connection Failures**: Check endpoints, authentication, network connectivity
2. **High Latency**: Monitor server response times, network conditions
3. **Memory Leaks**: Ensure proper cleanup, monitor connection count
4. **Rate Limiting**: Adjust request rates, respect exchange limits
5. **Message Loss**: Check queue sizes, compression settings

### Debug Tools
```python
# Enable detailed logging
import logging
logging.getLogger('bt_api_py.websocket').setLevel(logging.DEBUG)

# Get connection health
health = connection.get_health()
print(f"Health score: {health.health_score}")

# Get performance metrics
metrics = connection.get_metrics()
print(f"Avg latency: {metrics.get_avg_latency()}ms")

# Get pool statistics
stats = await get_websocket_stats()
print(f"Pool stats: {stats}")
```

## 📚 API Reference

### Core Classes
- `WebSocketConfig`: Connection configuration
- `PoolConfiguration`: Pool management settings
- `AdvancedWebSocketManager`: Connection pool manager
- `WebSocketMonitor`: Monitoring and alerting system

### Exchange Adapters
- `ExchangeWebSocketAdapter`: Base adapter interface
- `BinanceWebSocketAdapter`: Binance-specific implementation
- `OKXWebSocketAdapter`: OKX-specific implementation
- `WebSocketAdapterFactory`: Adapter factory

### Monitoring Components
- `MetricsCollector`: Metrics collection and storage
- `AlertManager`: Alert management and notification
- `WebSocketBenchmark`: Performance benchmarking tools

## 🔗 Integration Examples

### Trading Bot Integration
```python
class TradingBot:
    def __init__(self):
        self.ws_manager = None
        self.subscriptions = {}
    
    async def start(self):
        self.ws_manager = await get_websocket_manager()
        
        # Subscribe to market data
        await self.subscribe_market_data("BTCUSDT")
        await self.subscribe_market_data("ETHUSDT")
    
    async def handle_ticker(self, data):
        # Process ticker data for trading decisions
        symbol = data['symbol']
        price = data['last_price']
        # Trading logic here
```

### Real-time Dashboard
```python
import asyncio
import json
from bt_api_py.websocket import get_websocket_monitor

async def dashboard():
    monitor = await get_websocket_monitor()
    
    while True:
        dashboard = await get_monitoring_dashboard()
        
        # Update UI or send to monitoring system
        print(json.dumps(dashboard, indent=2))
        await asyncio.sleep(5)
```

## 📄 License

This WebSocket optimization system is part of the bt_api_py project and follows the same licensing terms.

## 🤝 Contributing

Contributions are welcome! Please see the main project's contribution guidelines for details on:
- Adding new exchange adapters
- Improving performance monitoring
- Enhancing error handling
- Adding new features

## 📞 Support

For issues and questions:
1. Check the troubleshooting section
2. Review test cases for similar scenarios
3. Enable debug logging for detailed information
4. Check exchange-specific documentation for rate limits and features