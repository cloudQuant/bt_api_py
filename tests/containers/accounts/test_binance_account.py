# import json
from btpy.containers.accounts.binance_account import (BinanceSwapRequestAccountData,
                                                      BinanceSwapWssAccountData,
                                                      BinanceSpotRequestAccountData,
                                                      BinanceSpotWssAccountData)
from btpy.containers.positions.binance_position import BinanceRequestPositionData, BinanceWssPositionData
from btpy.containers.balances.binance_balance import BinanceSwapRequestBalanceData


def test_binance_swap_wss_account():
    data = {'e': 'outboundAccountPosition', 'E': 1709103527220, 'u': 1709103527220,
            'B': [{'a': 'BNB', 'f': '0.00000000', 'l': '0.00000000'},
                  {'a': 'USDT', 'f': '29.24200000', 'l': '6.75800000'},
                  {'a': 'OP', 'f': '0.00000000', 'l': '0.00000000'}]}
    spot_wss_account = BinanceSpotWssAccountData(data, "ALL", "SPOT", True)
    spot_wss_account.init_data()
    assert spot_wss_account.get_server_time() == 1709103527220.0


def test_binance_spot_request_account():
    data = {'makerCommission': 10, 'takerCommission': 10, 'buyerCommission': 0, 'sellerCommission': 0,
            'commissionRates': {'maker': '0.00100000', 'taker': '0.00100000', 'buyer': '0.00000000',
                                'seller': '0.00000000'}, 'canTrade': True, 'canWithdraw': True, 'canDeposit': True,
            'brokered': False, 'requireSelfTradePrevention': False, 'preventSor': False, 'updateTime': 1705653333722,
            'accountType': 'SPOT', 'balances': [{'asset': 'BTC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LTC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ETH', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NEO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BNB', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'QTUM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'EOS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SNT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BNT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GAS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BCC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'USDT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'HSR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'OAX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DNT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MCO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ICN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ZRX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'OMG', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'WTC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'YOYO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LRC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TRX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SNGLS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'STRAT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BQX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FUN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'KNC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CDT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XVG', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'IOTA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SNM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LINK', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CVC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TNT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'REP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MDA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MTL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SALT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NULS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SUB', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'STX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MTH', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ADX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ETC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ENG', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ZEC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AST', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GNT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DGD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BAT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DASH', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'POWR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BTG', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'REQ', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XMR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'EVX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'VIB', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ENJ', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'VEN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ARK', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XRP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MOD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'STORJ', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'KMD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RCN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'EDO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DATA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DLT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MANA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PPT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RDN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GXS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AMB', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ARN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BCPT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CND', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GVT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'POE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BTS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FUEL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XZC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'QSP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LSK', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BCD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TNB', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ADA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LEND', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XLM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CMT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'WAVES', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'WABI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GTO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ICX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'OST', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ELF', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AION', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'WINGS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BRD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NEBL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NAV', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'VIBE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LUN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TRIG', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'APPC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CHAT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RLC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'INS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PIVX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'IOST', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'STEEM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NANO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'VIA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BLZ', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SYS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RPX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NCASH', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'POA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ONT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ZIL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'STORM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XEM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'WAN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'WPR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'QLC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GRS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CLOAK', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LOOM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BCN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TUSD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ZEN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SKY', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'THETA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'IOTX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'QKC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AGI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NXS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NPXS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'KEY', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NAS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MFT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DENT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'IQ', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ARDR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'HOT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'VET', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DOCK', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'POLY', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'VTHO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ONG', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PHX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'HC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PAX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RVN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DCR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'USDC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MITH', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BCHABC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BCHSV', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'REN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BTT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'USDS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FET', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TFUEL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CELR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MATIC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ATOM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PHB', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ONE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FTM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BTCB', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'USDSB', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CHZ', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'COS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ALGO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ERD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DOGE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BGBP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DUSK', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ANKR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'WIN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TUSDB', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'COCOS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PERL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TOMO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BUSD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BAND', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BEAM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'HBAR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XTZ', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NGN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DGB', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NKN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GBP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'EUR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'KAVA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RUB', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'UAH', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ARPA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TRY', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CTXC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AERGO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BCH', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TROY', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ARS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BRL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'VITE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FTT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PLN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RON', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AUD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'OGN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DREP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BULL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BEAR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ETHBULL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ETHBEAR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XRPBULL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XRPBEAR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'EOSBULL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'EOSBEAR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TCT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'WRX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LTO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ZAR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MBL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'COTI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BKRW', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BNBBULL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BNBBEAR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'HIVE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'STPT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SOL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'IDRT', 'free': '0.00', 'locked': '0.00'},
                                                {'asset': 'CTSI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CHR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BTCUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BTCDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'HNT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'JST', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FIO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BIDR', 'free': '0.00', 'locked': '0.00'},
                                                {'asset': 'STMX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MDT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PNT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'COMP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'IRIS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MKR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SXP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SNX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DAI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ETHUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ETHDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ADAUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ADADOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LINKUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LINKDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DOT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RUNE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BNBUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BNBDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XTZUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XTZDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AVA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BAL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'YFI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SRM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ANT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CRV', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SAND', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'OCEAN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NMR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LUNA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'IDEX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RSR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PAXG', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'WNXM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TRB', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'EGLD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BZRX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'WBTC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'KSM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SUSHI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'YFII', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DIA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BEL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'UMA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'EOSUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TRXUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'EOSDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TRXDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XRPUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XRPDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DOTUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DOTDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NBS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'WING', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SWRV', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LTCUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LTCDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CREAM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'UNI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'OXT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SUN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AVAX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BURGER', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BAKE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FLM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SCRT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XVS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CAKE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SPARTA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'UNIUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'UNIDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ALPHA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ORN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'UTK', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NEAR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'VIDT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AAVE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FIL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SXPUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SXPDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'INJ', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FILDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FILUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'YFIUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'YFIDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CTK', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'EASY', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AUDIO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BCHUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BCHDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BOT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AXS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AKRO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'HARD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'KP3R', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RENBTC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SLP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'STRAX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'UNFI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CVP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BCHA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FOR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FRONT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ROSE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MDX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'HEGIC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AAVEUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AAVEDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PROM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BETH', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SKL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GLM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SUSD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'COVER', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GHST', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SUSHIUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SUSHIDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XLMUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XLMDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DF', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'JUV', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PSG', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BVND', 'free': '0.00', 'locked': '0.00'},
                                                {'asset': 'GRT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CELO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TWT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'REEF', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'OG', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ATM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ASR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': '1INCH', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RIF', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BTCST', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TRU', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DEXE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CKB', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FIRO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LIT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PROS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'VAI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SFP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FXS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DODO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AUCTION', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'UFT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ACM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PHA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TVK', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BADGER', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FIS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'QI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'OM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'POND', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ALICE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DEGO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BIFI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LINA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PERP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RAMP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SUPER', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CFX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TKO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AUTO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'EPS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PUNDIX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TLM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': '1INCHUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': '1INCHDOWN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MIR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BAR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FORTH', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'EZ', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ICP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SHIB', 'free': '0.00', 'locked': '0.00'},
                                                {'asset': 'POLS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MASK', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LPT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AGIX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ATA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NU', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GTC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'KLAY', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TORN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'KEEP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ERN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BOND', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MLN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'C98', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FLOW', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'QUICK', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RAY', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MINA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'QNT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CLV', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XEC', 'free': '0.00', 'locked': '0.00'},
                                                {'asset': 'ALPACA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FARM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'VGX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MBOX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'WAXP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TRIBE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GNO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'USDP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DYDX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GALA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ILV', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'YGG', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FIDA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AGLD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BETA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RAD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RARE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SSV', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LAZIO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MOVR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CHESS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DAR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ACA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ASTR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BNX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RGT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CITY', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ENS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PORTO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'JASMY', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AMP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PLA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PYR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SANTOS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RNDR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ALCX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ANY', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'VOXEL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BICO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FLUX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'UST', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'HIGH', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'OOKI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CVX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PEOPLE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SPELL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'JOE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BDOT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GLMR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ACH', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'IMX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LOKA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BTTC', 'free': '0.0', 'locked': '0.0'},
                                                {'asset': 'ANC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'API3', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XNO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'WOO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ALPINE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'T', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NBT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'KDA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'APE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GMT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MOB', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BSW', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MULTI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'REI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GAL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NEXO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'EPX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LDO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'USTC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LUNC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'OP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LEVER', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'STG', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'POLYX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GMX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'APT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'OSMO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'HFT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'HOOK', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MAGIC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'HIFI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RPL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GFT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'GNS', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SYN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'LQTY', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ID', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ARB', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RDNT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'EDU', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'WBETH', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SUI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PEPE', 'free': '0.00', 'locked': '0.00'},
                                                {'asset': 'FLOKI', 'free': '0.00', 'locked': '0.00'},
                                                {'asset': 'COMBO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'NTRN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MAV', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'FDUSD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PENDLE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ARKM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'WLD', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'CYBER', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'SEI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AEUR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ORDI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MEME', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'TIA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BEAMX', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'VIC', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'BLUR', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'VANRY', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'JTO', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ACE', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': '1000SATS', 'free': '0.00', 'locked': '0.00'},
                                                {'asset': 'BONK', 'free': '0.00', 'locked': '0.00'},
                                                {'asset': 'NFP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'AI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'XAI', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'MANTA', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'ALT', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'RONIN', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'JUP', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PYTH', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'DYM', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'PIXEL', 'free': '0.00000000', 'locked': '0.00000000'},
                                                {'asset': 'STRK', 'free': '0.00000000', 'locked': '0.00000000'}],
            'permissions': ['SPOT'], 'uid': 831892118}

    account_data = BinanceSpotRequestAccountData(data, "ALL", "SPOT", True)
    account_data.init_data()
    assert isinstance(account_data, BinanceSpotRequestAccountData)


def test_binance_wss_account():
    data = {
        "e": "ACCOUNT_UPDATE",  # 事件类型
        "E": 1564745798939,  # 事件时间
        "T": 1564745798938,  # 撮合时间
        # 账户更新事件
        "a": {
            "m": "ORDER",  # 事件推出原因
            "B": [  # 余额信息
                {
                    "a": "USDT",  # 资产名称
                    "wb": "122624.12345678",  # 钱包余额
                    "cw": "100.12345678",  # 除去逐仓仓位保证金的钱包余额
                    "bc": "50.12345678"  # 除去盈亏与交易手续费以外的钱包余额改变量
                },
                {
                    "a": "BUSD",
                    "wb": "1.00000000",
                    "cw": "0.00000000",
                    "bc": "-49.12345678"
                }
            ],
            "P": [
                {
                    "s": "BTCUSDT",  # 交易对
                    "pa": "0",  # 仓位
                    "ep": "0.00000",  # 入仓价格
                    "bep": "0",  # 盈亏平衡价
                    "cr": "200",  # (费前)累计实现损益
                    "up": "0",  # 持仓未实现盈亏
                    "mt": "isolated",  # 保证金模式
                    "iw": "0.00000000",  # 若为逐仓，仓位保证金
                    "ps": "BOTH"  # 持仓方向
                },
                {
                    "s": "BTCUSDT",
                    "pa": "20",
                    "ep": "6563.66500",
                    "bep": "6563.6",
                    "cr": "0",
                    "up": "2850.21200",
                    "mt": "isolated",
                    "iw": "13200.70726908",
                    "ps": "LONG"
                },
                {
                    "s": "BTCUSDT",
                    "pa": "-10",
                    "ep": "6563.86000",
                    "bep": "6563.6",
                    "cr": "-45.04000000",
                    "up": "-1423.15600",
                    "mt": "isolated",
                    "iw": "6570.42511771",
                    "ps": "SHORT"
                }
            ]
        }
    }
    bo = BinanceSwapWssAccountData(data, None, "PERPETUAL", True)
    bo.init_data()
    p2 = bo.get_positions()[2]
    p2.init_data()
    p0 = bo.get_positions()[0]
    p0.init_data()
    assert p2.get_position_unrealized_pnl() == -1423.15600
    assert p0.get_position_realized_pnl() == 200.0
    assert isinstance(bo.get_positions()[0], BinanceWssPositionData)
    assert bo.get_option_taker_commission_rate() is None
    assert bo.get_option_maker_commission_rate() is None
    assert bo.get_future_taker_commission_rate() is None
    assert bo.get_future_maker_commission_rate() is None
    assert bo.get_spot_taker_commission_rate() is None
    assert bo.get_spot_maker_commission_rate() is None
    assert bo.get_total_wallet_balance() is None
    assert bo.get_total_unrealized_profit() is None
    assert bo.get_total_position_initial_margin() is None
    assert bo.get_total_open_order_initial_margin() is None
    assert bo.get_total_available_margin() is None
    assert bo.get_total_maintain_margin() is None
    assert bo.get_total_margin() is None
    assert bo.get_max_withdraw_amount() is None
    assert bo.get_fee_tier() is None
    assert bo.get_can_withdraw() is None
    assert bo.get_can_trade() is None
    assert bo.get_can_deposit() is None
    assert bo.get_account_type() is None
    assert bo.get_account_id() is None
    assert isinstance(bo.get_local_update_time(), float)
    assert bo.get_server_time() == 1564745798939.0
    assert bo.get_exchange_name() == 'BINANCE'
    assert bo.get_asset_type() == "PERPETUAL"


def test_binance_req_single_asset_account():
    data = {
        "feeTier": 0,  # 手续费等级
        "canTrade": 'true',  # 是否可以交易
        "canDeposit": 'true',  # 是否可以入金
        "canWithdraw": 'true',  # 是否可以出金
        "updateTime": 0,  # 保留字段，请忽略
        "multiAssetsMargin": 'false',
        "tradeGroupId": -1,
        "totalInitialMargin": "0.00000000",  # 当前所需起始保证金总额(存在逐仓请忽略), 仅计算usdt资产
        "totalMaintMargin": "0.00000000",  # 维持保证金总额, 仅计算usdt资产
        "totalWalletBalance": "23.72469206",  # 账户总余额, 仅计算usdt资产
        "totalUnrealizedProfit": "0.00000000",  # 持仓未实现盈亏总额, 仅计算usdt资产
        "totalMarginBalance": "23.72469206",  # 保证金总余额, 仅计算usdt资产
        "totalPositionInitialMargin": "0.00000000",  # 持仓所需起始保证金(基于最新标记价格), 仅计算usdt资产
        "totalOpenOrderInitialMargin": "0.00000000",  # 当前挂单所需起始保证金(基于最新标记价格), 仅计算usdt资产
        "totalCrossWalletBalance": "23.72469206",  # 全仓账户余额, 仅计算usdt资产
        "totalCrossUnPnl": "0.00000000",  # 全仓持仓未实现盈亏总额, 仅计算usdt资产
        "availableBalance": "23.72469206",  # 可用余额, 仅计算usdt资产
        "maxWithdrawAmount": "23.72469206",  # 最大可转出余额, 仅计算usdt资产
        "assets": [
            {
                "asset": "USDT",  # 资产
                "walletBalance": "23.72469206",  # 余额
                "unrealizedProfit": "0.00000000",  # 未实现盈亏
                "marginBalance": "23.72469206",  # 保证金余额
                "maintMargin": "0.00000000",  # 维持保证金
                "initialMargin": "0.00000000",  # 当前所需起始保证金
                "positionInitialMargin": "0.00000000",  # 持仓所需起始保证金(基于最新标记价格)
                "openOrderInitialMargin": "0.00000000",  # 当前挂单所需起始保证金(基于最新标记价格)
                "crossWalletBalance": "23.72469206",  # 全仓账户余额
                "crossUnPnl": "0.00000000",  # 全仓持仓未实现盈亏
                "availableBalance": "126.72469206",  # 可用余额
                "maxWithdrawAmount": "23.72469206",  # 最大可转出余额
                "marginAvailable": 'true',  # 是否可用作联合保证金
                "updateTime": 1625474304765  # 更新时间
            },
            {
                "asset": "BUSD",  # 资产
                "walletBalance": "103.12345678",  # 余额
                "unrealizedProfit": "0.00000000",  # 未实现盈亏
                "marginBalance": "103.12345678",  # 保证金余额
                "maintMargin": "0.00000000",  # 维持保证金
                "initialMargin": "0.00000000",  # 当前所需起始保证金
                "positionInitialMargin": "0.00000000",  # 持仓所需起始保证金(基于最新标记价格)
                "openOrderInitialMargin": "0.00000000",  # 当前挂单所需起始保证金(基于最新标记价格)
                "crossWalletBalance": "103.12345678",  # 全仓账户余额
                "crossUnPnl": "0.00000000",  # 全仓持仓未实现盈亏
                "availableBalance": "126.72469206",  # 可用余额
                "maxWithdrawAmount": "103.12345678",  # 最大可转出余额
                "marginAvailable": 'true',  # 否可用作联合保证金
                "updateTime": 0  # 更新时间
            }
        ],
        "positions": [  # 头寸，将返回所有市场symbol。
            # 根据用户持仓模式展示持仓方向，即单向模式下只返回BOTH持仓情况，双向模式下只返回 LONG 和 SHORT 持仓情况
            {
                "symbol": "BTCUSDT",  # 交易对
                "initialMargin": "0",  # 当前所需起始保证金(基于最新标记价格)
                "maintMargin": "0",  # 维持保证金
                "unrealizedProfit": "0.00000000",  # 持仓未实现盈亏
                "positionInitialMargin": "0",  # 持仓所需起始保证金(基于最新标记价格)
                "openOrderInitialMargin": "0",  # 当前挂单所需起始保证金(基于最新标记价格)
                "leverage": "100",  # 杠杆倍率
                "isolated": 'true',  # 是否是逐仓模式
                "entryPrice": "0.00000",  # 持仓成本价
                "maxNotional": "250000",  # 当前杠杆下用户可用的最大名义价值
                "bidNotional": "0",  # 买单净值，忽略
                "askNotional": "0",  # 卖单净值，忽略
                "positionSide": "BOTH",  # 持仓方向
                "positionAmt": "0",  # 持仓数量
                "updateTime": 0  # 更新时间
            }
        ]
    }
    bo = BinanceSwapRequestAccountData(data, None, "PERPETUAL", True)
    bo.init_data()
    assert isinstance(bo.get_positions()[0], BinanceRequestPositionData)
    assert isinstance(bo.get_balances()[0], BinanceSwapRequestBalanceData)
    assert bo.get_total_wallet_balance() == 23.72469206
    assert bo.get_total_unrealized_profit() == 0.0
    assert bo.get_total_position_initial_margin() == 0.0
    assert bo.get_total_open_order_initial_margin() == 0.0
    assert bo.get_total_available_margin() == 23.72469206
    assert bo.get_total_maintain_margin() == 0.0
    assert bo.get_total_margin() == 23.72469206
    assert bo.get_max_withdraw_amount() == 23.72469206
    assert bo.get_fee_tier() == 0.0
    assert bo.get_can_withdraw() is True
    assert bo.get_can_trade() is True
    assert bo.get_can_deposit() is True
    assert bo.get_account_type() is None
    assert bo.get_is_multi_assets_margin() is False
    assert bo.get_account_id() is None
    assert isinstance(bo.get_local_update_time(), float)
    assert bo.get_server_time() == 0.0
    assert bo.get_exchange_name() == 'BINANCE'
    assert bo.get_asset_type() == "PERPETUAL"


def test_binance_req_multi_asset_account():
    data = {
        "feeTier": 0,  # 手续费等级
        "canTrade": 'true',  # 是否可以交易
        "canDeposit": 'true',  # 是否可以入金
        "canWithdraw": 'true',  # 是否可以出金
        "updateTime": 0,  # 保留字段，请忽略
        "multiAssetsMargin": 'true',
        "tradeGroupId": -1,
        "totalInitialMargin": "0.00000000",  # 以USD计价的所需起始保证金总额
        "totalMaintMargin": "0.00000000",  # 以USD计价的维持保证金总额
        "totalWalletBalance": "126.72469206",  # 以USD计价的账户总余额
        "totalUnrealizedProfit": "0.00000000",  # 以USD计价的持仓未实现盈亏总额
        "totalMarginBalance": "126.72469206",  # 以USD计价的保证金总余额
        "totalPositionInitialMargin": "0.00000000",  # 以USD计价的持仓所需起始保证金(基于最新标记价格)
        "totalOpenOrderInitialMargin": "0.00000000",  # 以USD计价的当前挂单所需起始保证金(基于最新标记价格)
        "totalCrossWalletBalance": "126.72469206",  # 以USD计价的全仓账户余额
        "totalCrossUnPnl": "0.00000000",  # 以USD计价的全仓持仓未实现盈亏总额
        "availableBalance": "126.72469206",  # 以USD计价的可用余额
        "maxWithdrawAmount": "126.72469206",  # 以USD计价的最大可转出余额
        "assets": [
            {
                "asset": "USDT",  # 资产
                "walletBalance": "23.72469206",  # 余额
                "unrealizedProfit": "0.00000000",  # 未实现盈亏
                "marginBalance": "23.72469206",  # 保证金余额
                "maintMargin": "0.00000000",  # 维持保证金
                "initialMargin": "0.00000000",  # 当前所需起始保证金
                "positionInitialMargin": "0.00000000",  # 持仓所需起始保证金(基于最新标记价格)
                "openOrderInitialMargin": "0.00000000",  # 当前挂单所需起始保证金(基于最新标记价格)
                "crossWalletBalance": "23.72469206",  # 全仓账户余额
                "crossUnPnl": "0.00000000",  # 全仓持仓未实现盈亏
                "availableBalance": "23.72469206",  # 可用余额
                "maxWithdrawAmount": "23.72469206",  # 最大可转出余额
                "marginAvailable": 'true',  # 是否可用作联合保证金
                "updateTime": 1625474304765  # 更新时间
            },
            {
                "asset": "BUSD",  # 资产
                "walletBalance": "103.12345678",  # 余额
                "unrealizedProfit": "0.00000000",  # 未实现盈亏
                "marginBalance": "103.12345678",  # 保证金余额
                "maintMargin": "0.00000000",  # 维持保证金
                "initialMargin": "0.00000000",  # 当前所需起始保证金
                "positionInitialMargin": "0.00000000",  # 持仓所需起始保证金(基于最新标记价格)
                "openOrderInitialMargin": "0.00000000",  # 当前挂单所需起始保证金(基于最新标记价格)
                "crossWalletBalance": "103.12345678",  # 全仓账户余额
                "crossUnPnl": "0.00000000",  # 全仓持仓未实现盈亏
                "availableBalance": "103.12345678",  # 可用余额
                "maxWithdrawAmount": "103.12345678",  # 最大可转出余额
                "marginAvailable": 'true',  # 否可用作联合保证金
                "updateTime": 0  # 更新时间
            }
        ],
        "positions": [  # 头寸，将返回所有市场symbol。
            # 根据用户持仓模式展示持仓方向，即单向模式下只返回BOTH持仓情况，双向模式下只返回 LONG 和 SHORT 持仓情况
            {
                "symbol": "BTCUSDT",  # 交易对
                "initialMargin": "0",  # 当前所需起始保证金(基于最新标记价格)
                "maintMargin": "0",  # 维持保证金
                "unrealizedProfit": "0.00000000",  # 持仓未实现盈亏
                "positionInitialMargin": "0",  # 持仓所需起始保证金(基于最新标记价格)
                "openOrderInitialMargin": "0",  # 当前挂单所需起始保证金(基于最新标记价格)
                "leverage": "100",  # 杠杆倍率
                "isolated": 'true',  # 是否是逐仓模式
                "entryPrice": "0.00000",  # 持仓成本价
                "breakEvenPrice": "0.0",  # 持仓成本价
                "maxNotional": "250000",  # 当前杠杆下用户可用的最大名义价值
                "bidNotional": "0",  # 买单净值，忽略
                "askNotional": "0",  # 买单净值，忽略
                "positionSide": "BOTH",  # 持仓方向
                "positionAmt": "0",  # 持仓数量
                "updateTime": 0  # 更新时间
            }
        ]
    }
    bo = BinanceSwapRequestAccountData(data, None, "PERPETUAL", True)
    bo.init_data()
    assert bo.get_total_wallet_balance() == 126.72469206
    assert bo.get_total_unrealized_profit() == 0.0
    assert bo.get_total_position_initial_margin() == 0.0
    assert bo.get_total_open_order_initial_margin() == 0.0
    assert bo.get_total_available_margin() == 126.72469206
    assert bo.get_total_maintain_margin() == 0.0
    assert bo.get_total_margin() == 126.72469206
    assert bo.get_max_withdraw_amount() == 126.72469206
    assert bo.get_fee_tier() == 0.0
    assert bo.get_can_withdraw() is True
    assert bo.get_can_trade() is True
    assert bo.get_can_deposit() is True
    assert bo.get_account_type() is None
    assert bo.get_account_id() is None
    assert bo.get_is_multi_assets_margin() is True
    assert isinstance(bo.get_local_update_time(), float)
    assert bo.get_server_time() == 0.0
    assert bo.get_exchange_name() == 'BINANCE'
    assert bo.get_asset_type() == "PERPETUAL"
    assert isinstance(bo.get_balances()[0], BinanceSwapRequestBalanceData)
    assert isinstance(bo.get_positions()[0], BinanceRequestPositionData)


if __name__ == "__main__":
    test_binance_wss_account()
    test_binance_req_single_asset_account()
    test_binance_req_multi_asset_account()
