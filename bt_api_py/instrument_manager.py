"""
InstrumentManager — 统一交易标的管理器

提供 Instrument 注册、查询、双向映射功能。
"""

import threading

from bt_api_py.containers.instrument import AssetType, Instrument

__all__ = ["InstrumentManager", "get_instrument_manager"]


class InstrumentManager:
    """Instrument 管理器"""

    def __init__(self) -> None:
        self._instruments: dict[str, Instrument] = {}  # internal -> Instrument
        self._by_venue: dict[
            str, dict[str, Instrument]
        ] = {}  # venue -> {venue_symbol -> Instrument}
        self._by_underlying: dict[str, list[Instrument]] = {}  # underlying -> [Instrument]
        self._lock = threading.RLock()

    def register(self, instrument: Instrument) -> None:
        """注册 Instrument"""
        with self._lock:
            self._register_unlocked(instrument)

    def register_many(self, instruments: list[Instrument]) -> None:
        """批量注册"""
        with self._lock:
            for inst in instruments:
                self._register_unlocked(inst)

    def _register_unlocked(self, instrument: Instrument) -> None:
        """注册 Instrument（调用方需持有 _lock）"""
        previous = self._instruments.get(instrument.internal)
        if previous is not None:
            venue_instruments = self._by_venue.get(previous.venue)
            if venue_instruments is not None:
                venue_instruments.pop(previous.venue_symbol, None)
                if not venue_instruments:
                    self._by_venue.pop(previous.venue, None)

            if previous.underlying:
                underlying_instruments = self._by_underlying.get(previous.underlying)
                if underlying_instruments is not None:
                    filtered = [
                        inst
                        for inst in underlying_instruments
                        if inst.internal != previous.internal
                    ]
                    if filtered:
                        self._by_underlying[previous.underlying] = filtered
                    else:
                        self._by_underlying.pop(previous.underlying, None)

        self._instruments[instrument.internal] = instrument
        self._by_venue.setdefault(instrument.venue, {})[instrument.venue_symbol] = instrument
        if instrument.underlying:
            self._by_underlying.setdefault(instrument.underlying, []).append(instrument)

    def get(self, internal: str) -> Instrument | None:
        """获取 Instrument（通过内部符号）"""
        with self._lock:
            return self._instruments.get(internal)

    def get_by_venue(self, venue: str, venue_symbol: str) -> Instrument | None:
        """获取 Instrument（通过场所符号）"""
        with self._lock:
            return self._by_venue.get(venue, {}).get(venue_symbol)

    def find(
        self,
        venue: str | None = None,
        underlying: str | None = None,
        asset_type: AssetType | None = None,
        active_only: bool = True,
    ) -> list[Instrument]:
        """查找符合条件的 Instrument"""
        with self._lock:
            results = list(self._instruments.values())

        if venue:
            results = [i for i in results if i.venue == venue]
        if underlying:
            results = [i for i in results if i.underlying == underlying]
        if asset_type:
            results = [i for i in results if i.asset_type == asset_type]
        if active_only:
            results = [i for i in results if i.is_listed]

        return results

    def to_internal(self, venue: str, venue_symbol: str) -> str | None:
        """场所符号转换为内部符号"""
        instrument = self.get_by_venue(venue, venue_symbol)
        return instrument.internal if instrument else None

    def to_venue_symbol(self, internal: str) -> str | None:
        """内部符号转换为场所符号"""
        instrument = self.get(internal)
        return instrument.venue_symbol if instrument else None

    def all_internals(self) -> list[str]:
        """返回所有已注册的内部符号"""
        with self._lock:
            return list(self._instruments.keys())

    def all_venues(self) -> list[str]:
        """返回所有已注册的场所"""
        with self._lock:
            return list(self._by_venue.keys())

    def count(self) -> int:
        with self._lock:
            return len(self._instruments)

    def clear(self) -> None:
        """清空所有注册"""
        with self._lock:
            self._instruments.clear()
            self._by_venue.clear()
            self._by_underlying.clear()


# 全局单例
_instrument_manager = InstrumentManager()


def get_instrument_manager() -> InstrumentManager:
    """获取全局 InstrumentManager 单例"""
    return _instrument_manager
