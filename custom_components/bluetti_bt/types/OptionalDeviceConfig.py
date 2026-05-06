from typing import Any, Dict
import voluptuous as vol

CONF_POLLING_INTERVAL = "polling_interval"
CONF_POLLING_TIMEOUT = "polling_timeout"
CONF_MAX_RETRIES = "max_retries"
CONF_DC_INPUT_ENERGY = "dc_input_energy_enabled"
CONF_BATTERY_ENERGY = "battery_energy_enabled"

ABORT_REASON_INTERVAL = "invalid_interval"
ABORT_REASON_TIMEOUT = "invalid_timeout"
ABORT_REASON_RETRIES = "invalid_retries"


class OptionalDeviceConfig:
    def __init__(
        self,
        polling_interval: int,
        polling_timeout: int,
        max_retries: int,
        dc_input_energy_enabled: bool,
        battery_energy_enabled: bool,
    ):
        self.polling_interval = polling_interval
        self.polling_timeout = polling_timeout
        self.max_retries = max_retries
        self.dc_input_energy_enabled = dc_input_energy_enabled
        self.battery_energy_enabled = battery_energy_enabled

    @staticmethod
    def from_dict(raw: Dict[str, Any]):
        return OptionalDeviceConfig(
            raw.get(CONF_POLLING_INTERVAL, 20),
            raw.get(CONF_POLLING_TIMEOUT, 45),
            raw.get(CONF_MAX_RETRIES, 5),
            raw.get(CONF_DC_INPUT_ENERGY, True),
            raw.get(CONF_BATTERY_ENERGY, True),
        )

    def validate(self) -> str | None:
        if self.polling_interval < 5:
            return ABORT_REASON_INTERVAL
        if self.polling_timeout < 1:
            return ABORT_REASON_TIMEOUT
        if self.max_retries < 1:
            return ABORT_REASON_RETRIES
        return None

    @property
    def as_dict(self) -> Dict[str, Any]:
        return {
            CONF_POLLING_INTERVAL: self.polling_interval,
            CONF_POLLING_TIMEOUT: self.polling_timeout,
            CONF_MAX_RETRIES: self.max_retries,
            CONF_DC_INPUT_ENERGY: self.dc_input_energy_enabled,
            CONF_BATTERY_ENERGY: self.battery_energy_enabled,
        }

    @property
    def schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(
                    CONF_POLLING_INTERVAL,
                    default=self.polling_interval,
                ): int,
                vol.Required(
                    CONF_POLLING_TIMEOUT,
                    default=self.polling_timeout,
                ): int,
                vol.Required(
                    CONF_MAX_RETRIES,
                    default=self.max_retries,
                ): int,
                vol.Required(
                    CONF_DC_INPUT_ENERGY,
                    default=self.dc_input_energy_enabled,
                ): bool,
                vol.Required(
                    CONF_BATTERY_ENERGY,
                    default=self.battery_energy_enabled,
                ): bool,
            }
        )
