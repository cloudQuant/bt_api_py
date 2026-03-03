"""
Bitfinex Funding Rate Data Container
"""

import json
import time
from bt_api_py.containers.fundingrates.funding_rate import FundingRateData


class BitfinexFundingRateData(FundingRateData):
    """Bitfinex Funding Rate Data Container"""

    def __init__(self, funding_rate_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(funding_rate_info, symbol_name, asset_type, has_been_json_encoded)
        self.exchange_name = "BITFINEX"
        self.local_update_time = time.time()


class BitfinexRequestFundingRateData(BitfinexFundingRateData):
    """Bitfinex Request Funding Rate Data"""

    def __init__(self, funding_rate_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(funding_rate_info, symbol_name, asset_type, has_been_json_encoded)
