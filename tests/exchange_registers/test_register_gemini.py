"""Tests for exchange_registers/register_gemini.py."""

from bt_api_py.exchange_registers import register_gemini


class TestRegisterGemini:
    """Tests for Gemini registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_gemini is not None
