# btpy

#### 介绍
- python api to binance, okx

#### 安装方法
1. 下载项目: 
    - 方法一:从github上克隆 `git clone https://gitee.com/yunjinqi/btpy.git`
    - 方法二:安装包解压到本地
2. 安装依赖: ` pip install -r ./btpy/requirements.txt`
3. 编译安装项目: `pip install ./btpy`

#### 运行测试
- 首先在btpy/configs文件夹下新建一个account_config.yaml用于配置账户信息,
   - 格式参考account_config_example.yaml
   - 为了跑通测试，需要binance,okx交易所的api, 并且需要在现货账户和合约账户里面都有10USDT
- 在btpy文件夹下，运行命令：
    - pytest tests -n 4  # 使用4个cpu运行测试
    - pytest tests -n 4 --picked  # 使用4个CPU仅仅测试新的未通过测试的test

#### 使用方法

- 待完善

