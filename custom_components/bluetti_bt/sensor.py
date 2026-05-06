"""Bluetti BT sensors."""

from __future__ import annotations
from enum import Enum
import logging
from decimal import Decimal
from datetime import datetime
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from bluetti_bt_lib import build_device, FieldName, get_unit

from . import device_info as dev_info, get_unique_id, FullDeviceConfig
from .const import DATA_COORDINATOR, DOMAIN, MANUFACTURER
from .coordinator import PollingCoordinator
from .utils import mac_loggable, unique_id_logable
from .types import get_device_class, get_state_class, get_category

# Skip integration when the gap between samples exceeds this many polling intervals
# (avoids inflating the running total across disconnects).
_DC_ENERGY_MAX_GAP_FACTOR = 5

_BATTERY_POWER_FIELDS = (
    FieldName.DC_INPUT_POWER,
    FieldName.AC_INPUT_POWER,
    FieldName.AC_OUTPUT_POWER,
    FieldName.DC_OUTPUT_POWER,
)


def _power_value(data: dict, key: str) -> float | None:
    v = data.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _battery_power(data: dict) -> float | None:
    """Derived battery power, in W. Positive = charging, negative = discharging."""
    dc_in = _power_value(data, FieldName.DC_INPUT_POWER.value)
    ac_in = _power_value(data, FieldName.AC_INPUT_POWER.value)
    ac_out = _power_value(data, FieldName.AC_OUTPUT_POWER.value)
    dc_out = _power_value(data, FieldName.DC_OUTPUT_POWER.value)
    if dc_in is None or ac_in is None or ac_out is None or dc_out is None:
        return None
    return (dc_in + ac_in) - (ac_out + dc_out)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Setup sensor entities."""

    config = FullDeviceConfig.from_dict(entry.data)
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    if config is None or not isinstance(coordinator, PollingCoordinator):
        logging.getLogger(__name__).error("No coordinator found")
        return None

    logger = logging.getLogger(
        f"{__name__}.{mac_loggable(config.address).replace(':', '_')}"
    )

    # Generate device info
    logger.info("Creating sensors for device with address %s", config.address)
    device_info = dev_info(entry)

    # Add sensors
    bluetti_device = build_device(config.name)

    sensors_to_add = []
    sensor_fields = bluetti_device.get_sensor_fields()

    if config.use_encryption:
        sensor_fields = sensor_fields + bluetti_device.get_select_fields()

    available_field_names = {FieldName(f.name) for f in sensor_fields}
    has_dc_input_power = FieldName.DC_INPUT_POWER in available_field_names
    has_all_battery_power_fields = all(
        f in available_field_names for f in _BATTERY_POWER_FIELDS
    )

    if config.dc_input_energy_enabled and has_dc_input_power:
        sensors_to_add.append(
            BluettiDcInputEnergySensor(
                coordinator,
                device_info,
                config.polling_interval,
                logger=logger,
            )
        )

    if config.battery_energy_enabled and has_all_battery_power_fields:
        sensors_to_add.extend(
            [
                BluettiBatteryPowerSensor(coordinator, device_info, logger=logger),
                BluettiBatteryChargeEnergySensor(
                    coordinator,
                    device_info,
                    config.polling_interval,
                    logger=logger,
                ),
                BluettiBatteryDischargeEnergySensor(
                    coordinator,
                    device_info,
                    config.polling_interval,
                    logger=logger,
                ),
            ]
        )

    for field in sensor_fields:
        field_name = FieldName(field.name)

        if field_name in [FieldName.PACK_CELL_VOLTAGES, FieldName.PACK_SELECTED]:
            continue

        unit = get_unit(field_name)
        device_class = get_device_class(field_name)
        state_class = get_state_class(field_name)
        category = None if config.use_encryption else get_category(field_name)

        if unit is not None:
            sensors_to_add.append(
                BluettiSensor(
                    coordinator,
                    device_info,
                    field.address,
                    field.name,
                    unit_of_measurement=unit,
                    device_class=device_class,
                    state_class=state_class,
                    category=category,
                    logger=logger,
                )
            )
        else:
            sensors_to_add.append(
                BluettiSensor(
                    coordinator,
                    device_info,
                    field.address,
                    field.name,
                    category=category,
                    logger=logger,
                )
            )

    # Pack fields
    for field in bluetti_device.pack_fields:
        field_name = FieldName(field.name)

        if field_name in [FieldName.PACK_SELECTED]:
            continue

        unit = get_unit(field_name)
        device_class = get_device_class(field_name)
        state_class = get_state_class(field_name)
        category = get_category(field_name)

        for num in range(1, bluetti_device.max_packs + 1):
            main_name = dev_info(entry).get("name")
            device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{config.address}_pack_{num}")},
                name=f"{main_name} Battery Pack {num}",
                manufacturer=MANUFACTURER,
            )

            if field_name == FieldName.PACK_CELL_VOLTAGES:
                # Special case: list of cell voltages
                for cell_num in range(1, field.size + 1):
                    sensors_to_add.append(
                        BluettiSensor(
                            coordinator,
                            device_info,
                            field.address,
                            field.name,
                            unit_of_measurement=unit,
                            device_class=device_class,
                            state_class=state_class,
                            category=category,
                            pack_num=num,
                            cell_num=cell_num,
                            logger=logger,
                        )
                    )
                continue

            if unit is not None:
                sensors_to_add.append(
                    BluettiSensor(
                        coordinator,
                        device_info,
                        field.address,
                        field.name,
                        unit_of_measurement=unit,
                        device_class=device_class,
                        state_class=state_class,
                        category=category,
                        pack_num=num,
                        logger=logger,
                    )
                )
            else:
                sensors_to_add.append(
                    BluettiSensor(
                        coordinator,
                        device_info,
                        field.address,
                        field.name,
                        category=category,
                        pack_num=num,
                        logger=logger,
                    )
                )

    async_add_entities(sensors_to_add)


class BluettiSensor(CoordinatorEntity, SensorEntity):
    """Bluetti universal sensor."""

    def __init__(
        self,
        coordinator: PollingCoordinator,
        device_info: DeviceInfo,
        address,
        response_key: str,
        unit_of_measurement: str | None = None,
        device_class: str | None = None,
        state_class: str | None = None,
        category: EntityCategory | None = None,
        options: list[str] | None = None,
        pack_num: int | None = None,
        cell_num: int | None = None,
        logger: logging.Logger = logging.getLogger(),
    ):
        """Init sensor entity."""
        super().__init__(coordinator)
        self.coordinator = coordinator
        self._pack_num = pack_num
        self._cell_num = cell_num
        self._logger = logger

        self._attr_has_entity_name = True
        e_name = f"{device_info.get('name')} {response_key}"

        if cell_num is not None:
            e_name = f"{device_info.get('name')} {response_key} {cell_num}"

        self._address = address
        self._response_key = (
            f"pack_{pack_num}_{response_key}" if pack_num else response_key
        )
        self._unavailable_counter = 0

        self._attr_device_info = device_info
        self._attr_translation_key = (
            f"pack_{response_key}" if pack_num else response_key
        )

        if cell_num is not None:
            self._attr_translation_key = f"pack_{response_key}"
            self._attr_translation_placeholders = {"cell_num": cell_num}

        self._attr_available = False
        self._attr_unique_id = get_unique_id(e_name)
        self._attr_native_unit_of_measurement = unit_of_measurement
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_entity_category = category
        self._options = options

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._attr_available

    def _set_available(self):
        """Set sensor as available."""
        self._attr_available = True
        self._unavailable_counter = 0
        self._attr_extra_state_attributes = {}
        self.async_write_ha_state()

    def _set_unavailable(self, cause: str = "Unknown"):
        """Set sensor as unavailable."""
        self._unavailable_counter += 1

        self._attr_extra_state_attributes = {
            "unavailable_counter": self._unavailable_counter,
            "unavailable_cause": cause,
        }

        if self._unavailable_counter >= 5:
            self._attr_available = False

        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""

        if self.coordinator.data is None:
            self._logger.debug(
                "Data from coordinator is None",
            )
            self._set_unavailable("Data is None")
            return

        if not isinstance(self.coordinator.data, dict):
            self._logger.warning(
                "Invalid data from coordinator (sensor.%s)",
                unique_id_logable(self._attr_unique_id),
            )
            self._set_unavailable("Invalid data")
            return

        self._logger.debug(
            "Coordinator data: %s",
            self.coordinator.data,
        )

        response_data = self.coordinator.data.get(self._response_key)
        if response_data is None:
            self._logger.debug("No data for available for (%s)", self._response_key)
            self._set_unavailable("No data")
            return

        if (
            not isinstance(response_data, int)
            and not isinstance(response_data, float)
            and not isinstance(response_data, complex)
            and not isinstance(response_data, Decimal)
            and not isinstance(response_data, Enum)
            and not isinstance(response_data, str)
            and not isinstance(response_data, list)
        ):
            self._logger.warning(
                "Invalid response data type from coordinator (sensor.%s): %s has type %s",
                unique_id_logable(self._attr_unique_id),
                response_data,
                type(response_data),
            )
            self._set_unavailable("Invalid data type")
            return

        if isinstance(response_data, list) and len(response_data) < self._cell_num:
            self._set_unavailable("Invalid list length")
            return

        self._set_available()

        # Different for enum and numeric
        if isinstance(response_data, Enum):
            # Enum
            self._attr_native_value = response_data.name
        elif isinstance(response_data, list):
            self._attr_native_value = response_data[self._cell_num - 1]
        else:
            # Numeric
            self._attr_native_value = response_data
        self.async_write_ha_state()


class _BluettiAccumulatingEnergySensor(CoordinatorEntity, RestoreSensor):
    """Trapezoidal-integration energy sensor, persists across restarts."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        coordinator: PollingCoordinator,
        device_info: DeviceInfo,
        polling_interval: int,
        translation_key: str,
        unique_suffix: str,
        logger: logging.Logger = logging.getLogger(),
    ):
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_translation_key = translation_key
        self._attr_unique_id = get_unique_id(
            f"{device_info.get('name')} {unique_suffix}"
        )
        self._logger = logger
        self._max_gap_seconds = max(
            polling_interval * _DC_ENERGY_MAX_GAP_FACTOR, 60
        )
        self._last_power: float | None = None
        self._last_ts: datetime | None = None
        self._total_kwh: float = 0.0
        self._attr_native_value = 0.0

    def _compute_power(self, data: dict) -> float | None:
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            try:
                self._total_kwh = float(last.native_value)
            except (TypeError, ValueError):
                self._total_kwh = 0.0
        self._attr_native_value = round(self._total_kwh, 3)

    @callback
    def _handle_coordinator_update(self) -> None:
        if not self.coordinator.last_update_success:
            return
        data = self.coordinator.data
        if not isinstance(data, dict):
            return
        power = self._compute_power(data)
        if power is None:
            return

        now = dt_util.utcnow()
        if self._last_power is not None and self._last_ts is not None:
            dt_seconds = (now - self._last_ts).total_seconds()
            if 0 < dt_seconds <= self._max_gap_seconds:
                avg_w = (self._last_power + power) / 2.0
                self._total_kwh += (avg_w * dt_seconds) / 3_600_000.0

        self._last_power = float(power)
        self._last_ts = now
        self._attr_native_value = round(self._total_kwh, 3)
        self.async_write_ha_state()


class BluettiDcInputEnergySensor(_BluettiAccumulatingEnergySensor):
    """Cumulative DC input energy derived from dc_input_power."""

    def __init__(
        self,
        coordinator: PollingCoordinator,
        device_info: DeviceInfo,
        polling_interval: int,
        logger: logging.Logger = logging.getLogger(),
    ):
        super().__init__(
            coordinator,
            device_info,
            polling_interval,
            translation_key="dc_input_energy",
            unique_suffix="dc_input_energy",
            logger=logger,
        )

    def _compute_power(self, data: dict) -> float | None:
        return _power_value(data, FieldName.DC_INPUT_POWER.value)


class BluettiBatteryChargeEnergySensor(_BluettiAccumulatingEnergySensor):
    """Cumulative energy charged into the battery (derived)."""

    def __init__(
        self,
        coordinator: PollingCoordinator,
        device_info: DeviceInfo,
        polling_interval: int,
        logger: logging.Logger = logging.getLogger(),
    ):
        super().__init__(
            coordinator,
            device_info,
            polling_interval,
            translation_key="battery_charge_energy",
            unique_suffix="battery_charge_energy",
            logger=logger,
        )

    def _compute_power(self, data: dict) -> float | None:
        p = _battery_power(data)
        if p is None:
            return None
        return max(p, 0.0)


class BluettiBatteryDischargeEnergySensor(_BluettiAccumulatingEnergySensor):
    """Cumulative energy discharged from the battery (derived)."""

    def __init__(
        self,
        coordinator: PollingCoordinator,
        device_info: DeviceInfo,
        polling_interval: int,
        logger: logging.Logger = logging.getLogger(),
    ):
        super().__init__(
            coordinator,
            device_info,
            polling_interval,
            translation_key="battery_discharge_energy",
            unique_suffix="battery_discharge_energy",
            logger=logger,
        )

    def _compute_power(self, data: dict) -> float | None:
        p = _battery_power(data)
        if p is None:
            return None
        return max(-p, 0.0)


class BluettiBatteryPowerSensor(CoordinatorEntity, SensorEntity):
    """Instantaneous battery power (derived). Positive = charging, negative = discharging."""

    _attr_has_entity_name = True
    _attr_translation_key = "battery_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: PollingCoordinator,
        device_info: DeviceInfo,
        logger: logging.Logger = logging.getLogger(),
    ):
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = get_unique_id(
            f"{device_info.get('name')} battery_power"
        )
        self._logger = logger

    @callback
    def _handle_coordinator_update(self) -> None:
        if not self.coordinator.last_update_success:
            return
        data = self.coordinator.data
        if not isinstance(data, dict):
            return
        p = _battery_power(data)
        if p is None:
            return
        self._attr_native_value = round(p, 1)
        self.async_write_ha_state()
