# 类型提示改进计划

## 执行日期
2026-03-08

## 当前状态

### 总体覆盖率
- **当前**: 10.7%
- **目标**: 90%
- **差距**: 79.3%

### 模块覆盖率排名

#### 优秀模块（>90%）
1. risk_management: 100.0% ✅
2. websocket: 97.0% ✅
3. logging_system: 96.0% ✅
4. monitoring: 96.0% ✅
5. security_compliance: 94.8% ✅

#### 良好模块（50-90%）
6. core: 80.8% 📊
7. errors: 53.3% 📊
8. root: 59.1% 📊

#### 需要改进模块（<50%）
9. functions: 16.4% ⚠️
10. exchange_registers: 2.4% ⚠️
11. ctp: 0.3% ⚠️
12. feeds: 1.6% ⚠️
13. containers: 4.6% ⚠️
14. utils: 0.0% ⚠️

## 改进策略

### 阶段1：核心API（高优先级）
**目标文件**：
1. bt_api.py (0/37) - 主入口
2. event_bus.py (0/5) - 事件总线
3. registry.py - 注册表
4. auth_config.py - 认证配置

**预计时间**: 1天

### 阶段2：容器模块（中优先级）
**重点文件**：
- orders/order.py
- positions/position.py
- tickers/ticker.py
- bars/bar.py
- orderbooks/orderbook.py

**预计时间**: 2-3天

### 阶段3：Feed模块（中优先级）
**重点文件**：
- feeds/abstract_feed.py
- feeds/base_stream.py
- feeds/live_binance/*.py
- feeds/live_okx/*.py

**预计时间**: 3-4天

### 阶段4：工具函数（低优先级）
**重点文件**：
- functions/utils.py
- functions/async_base.py
- functions/calculate_numbers.py

**预计时间**: 1天

## 实施方法

### 1. 自动检测
```bash
# 查找缺少类型提示的公开函数
python scripts/find_missing_hints.py
```

### 2. 添加类型提示
```python
# 示例
def get_ticker(self, symbol: str) -> dict[str, Any]:
    """获取ticker数据
    
    Args:
        symbol: 交易对符号
        
    Returns:
        ticker数据字典
    """
    pass
```

### 3. 验证
```bash
# 运行mypy检查
mypy bt_api_py/
```

## 成功标准

### 短期目标（1周）
- 核心API覆盖率 > 90%
- 容器模块覆盖率 > 70%
- 总体覆盖率 > 30%

### 中期目标（2周）
- 容器模块覆盖率 > 90%
- Feed模块覆盖率 > 50%
- 总体覆盖率 > 60%

### 长期目标（3周）
- 所有模块覆盖率 > 90%
- 总体覆盖率 > 90%
- mypy检查通过

## 进度跟踪

- [ ] 阶段1：核心API
- [ ] 阶段2：容器模块
- [ ] 阶段3：Feed模块
- [ ] 阶段4：工具函数

## 注意事项

1. **保持兼容性**: 不改变现有API签名
2. **使用现代语法**: 使用 Python 3.11+ 类型语法（如 `list[str]` 而非 `List[str]`）
3. **避免过度复杂**: 类型提示应简洁明了
4. **添加文档字符串**: 配合类型提示添加说明

## 工具支持

```bash
# 自动添加类型提示（可选）
pip install pyannotate

# 运行测试生成类型信息
pytest tests/ --collect-only

# 应用类型提示
pyannotate --type-info ./type_info.json -w bt_api_py/
```
