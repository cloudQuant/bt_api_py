"""
Bitfinex Funding Rate Data Container
"""

import time

from bt_api_py.containers.fundingrates.funding_rate import FundingRateData


class BitfinexFundingRateData(FundingRateData):
    """Bitfinex Funding Rate Data Container"""

    def __init__(
        self,
        funding_rate_info,
        symbol_name: str = "",
        asset_type: str = "",
        has_been_json_encoded: bool = False,
    ):
        super().__init__(funding_rate_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BITFINEX"
        self.local_update_time = time.time()


class BitfinexRequestFundingRateData(BitfinexFundingRateData):
    """Bitfinex Request Funding Rate Data"""

    def __init__(
        self,
        funding_rate_info,
        symbol_name: str = "",
        asset_type: str = "",
        has_been_json_encoded: bool = False,
    ):
        super().__init__(funding_rate_info, symbol_name, asset_type, has_been_json_encoded)
