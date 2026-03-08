"""
Tests for bt_api_py.core.services
Core service lifecycle and behavior tests
"""

import asyncio
import pytest

from bt_api_py.core.services import CacheService, ConnectionService, EventService


class TestEventService:
    """Test EventService lifecycle"""

    @pytest.mark.asyncio
    async def test_event_service_start_stop(self):
        """Test EventService initialization and start/stop"""
        service = EventService()
        await service.start()
        assert service._running is True
        await service.stop()
        assert service._running is False

    @pytest.mark.asyncio
    async def test_event_service_publish(self):
        """Test event publishing"""
        service = EventService()
        await service.start()
        
        received = []

        def callback(event):
            received.append(event)

        service.subscribe("test_topic", callback)
        service.publish("test_topic", {"data": "test"})
        
        await asyncio.sleep(0.1)
        assert len(received) == 1
        assert received[0] == {"data": "test"}
        
        await service.stop()

    @pytest.mark.asyncio
    async def test_event_service_subscribe_unsubscribe(self):
        """Test event subscription and unsubscription"""
        service = EventService()
        await service.start()

        received = []

        def callback(event):
            received.append(event)

        service.subscribe("test_topic", callback)
        service.unsubscribe("test_topic", callback)
        service.publish("test_topic", {"data": "test"})

        await asyncio.sleep(0.1)
        assert len(received) == 0

        await service.stop()


