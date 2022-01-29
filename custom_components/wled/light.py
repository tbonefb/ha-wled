"""Support for LED lights."""
from __future__ import annotations

from functools import partial
from typing import Any, Tuple, cast

import voluptuous as vol

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_TRANSITION,
    COLOR_MODE_BRIGHTNESS,
    COLOR_MODE_RGB,
    COLOR_MODE_RGBW,
    SUPPORT_EFFECT,
    SUPPORT_TRANSITION,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_COLOR_PRIMARY,
    ATTR_COLOR_SECONDARY,
    ATTR_COLOR_TERTIARY,
    ATTR_COLOR_NAME_PRIMARY,
    ATTR_COLOR_NAME_SECONDARY,
    ATTR_COLOR_NAME_TERTIARY,
    ATTR_ON,
    ATTR_SEGMENT_ID,
    DOMAIN,
    LOGGER,
    SERVICE_COLORS,
    COLOR_GROUP_PRIMARY,
    COLOR_GROUP_SECONDARY,
    COLOR_GROUP_TERTIARY,
)
from .coordinator import WLEDDataUpdateCoordinator
from .helpers import wled_exception_handler
from .models import WLEDEntity
from .color import color_name_to_rgb

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up WLED light based on a config entry."""
    coordinator: WLEDDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(
        SERVICE_COLORS,
        {
            vol.Exclusive(ATTR_COLOR_PRIMARY, COLOR_GROUP_PRIMARY): vol.All(
                vol.ExactSequence((cv.byte,) * 3), vol.Coerce(tuple)
            ),
            vol.Exclusive(ATTR_COLOR_SECONDARY, COLOR_GROUP_SECONDARY): vol.All(
                vol.ExactSequence((cv.byte,) * 3), vol.Coerce(tuple)
            ),
            vol.Exclusive(ATTR_COLOR_TERTIARY, COLOR_GROUP_TERTIARY): vol.All(
                vol.ExactSequence((cv.byte,) * 3), vol.Coerce(tuple)
            ),
            vol.Exclusive(ATTR_COLOR_NAME_PRIMARY, COLOR_GROUP_PRIMARY): cv.string,
            vol.Exclusive(ATTR_COLOR_NAME_SECONDARY, COLOR_GROUP_SECONDARY): cv.string,
            vol.Exclusive(ATTR_COLOR_NAME_TERTIARY, COLOR_GROUP_TERTIARY): cv.string,
        },
        "async_colors",
    )

    if coordinator.keep_master_light:
        async_add_entities([WLEDMasterLight(coordinator=coordinator)])

    update_segments = partial(
        async_update_segments,
        coordinator,
        set(),
        async_add_entities,
    )

    coordinator.async_add_listener(update_segments)
    update_segments()


class WLEDMasterLight(WLEDEntity, LightEntity):
    """Defines a WLED master light."""

    _attr_color_mode = COLOR_MODE_BRIGHTNESS
    _attr_icon = "mdi:led-strip-variant"
    _attr_supported_features = SUPPORT_TRANSITION

    def __init__(self, coordinator: WLEDDataUpdateCoordinator) -> None:
        """Initialize WLED master light."""
        super().__init__(coordinator=coordinator)
        self._attr_name = f"{coordinator.data.info.name} Master"
        self._attr_unique_id = coordinator.data.info.mac_address
        self._attr_supported_color_modes = {COLOR_MODE_BRIGHTNESS}

    @property
    def brightness(self) -> int | None:
        """Return the brightness of this light between 1..255."""
        return self.coordinator.data.state.brightness

    @property
    def is_on(self) -> bool:
        """Return the state of the light."""
        return bool(self.coordinator.data.state.on)

    @property
    def available(self) -> bool:
        """Return if this master light is available or not."""
        return self.coordinator.has_master_light and super().available

    @wled_exception_handler
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        transition = None
        if ATTR_TRANSITION in kwargs:
            # WLED uses 100ms per unit, so 10 = 1 second.
            transition = round(kwargs[ATTR_TRANSITION] * 10)

        await self.coordinator.wled.master(on=False, transition=transition)

    @wled_exception_handler
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        transition = None
        if ATTR_TRANSITION in kwargs:
            # WLED uses 100ms per unit, so 10 = 1 second.
            transition = round(kwargs[ATTR_TRANSITION] * 10)

        await self.coordinator.wled.master(
            on=True, brightness=kwargs.get(ATTR_BRIGHTNESS), transition=transition
        )

    async def async_colors(
        self,
        color_primary: None = None,
        color_secondary: None = None,
        color_tertiary: None = None,
        color_name_primary: None = None,
        color_name_secondary: None = None,
        color_name_tertiary: None = None,
    ) -> None:
        """Set the colors of a WLED light."""
        # Master light does not have an colors setting.


class WLEDSegmentLight(WLEDEntity, LightEntity):
    """Defines a WLED light based on a segment."""

    _attr_supported_features = SUPPORT_EFFECT | SUPPORT_TRANSITION
    _attr_icon = "mdi:led-strip-variant"

    def __init__(
        self,
        coordinator: WLEDDataUpdateCoordinator,
        segment: int,
    ) -> None:
        """Initialize WLED segment light."""
        super().__init__(coordinator=coordinator)
        self._rgbw = coordinator.data.info.leds.rgbw
        self._wv = coordinator.data.info.leds.wv
        self._segment = segment

        # Segment 0 uses a simpler name, which is more natural for when using
        # a single segment / using WLED with one big LED strip.
        self._attr_name = f"{coordinator.data.info.name} Segment {segment}"
        if segment == 0:
            self._attr_name = coordinator.data.info.name

        self._attr_unique_id = (
            f"{self.coordinator.data.info.mac_address}_{self._segment}"
        )

        self._attr_color_mode = COLOR_MODE_RGB
        self._attr_supported_color_modes = {COLOR_MODE_RGB}
        if self._rgbw and self._wv:
            self._attr_color_mode = COLOR_MODE_RGBW
            self._attr_supported_color_modes = {COLOR_MODE_RGBW}

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        try:
            self.coordinator.data.state.segments[self._segment]
        except IndexError:
            return False

        return super().available

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the state attributes of the entity."""
        segment = self.coordinator.data.state.segments[self._segment]
        return {
            ATTR_COLOR_PRIMARY: segment.color_primary,
            ATTR_COLOR_SECONDARY: segment.color_secondary,
            ATTR_COLOR_TERTIARY: segment.color_tertiary,
        }

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the color value."""
        return self.coordinator.data.state.segments[self._segment].color_primary[:3]

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """Return the color value."""
        return cast(
            tuple[int, int, int, int],
            self.coordinator.data.state.segments[self._segment].color_primary,
        )

    @property
    def effect(self) -> str | None:
        """Return the current effect of the light."""
        return self.coordinator.data.state.segments[self._segment].effect.name

    @property
    def brightness(self) -> int | None:
        """Return the brightness of this light between 1..255."""
        state = self.coordinator.data.state

        # If this is the one and only segment, calculate brightness based
        # on the master and segment brightness
        if not self.coordinator.has_master_light:
            return int(
                (state.segments[self._segment].brightness * state.brightness) / 255
            )

        return state.segments[self._segment].brightness

    @property
    def effect_list(self) -> list[str]:
        """Return the list of supported effects."""
        return [effect.name for effect in self.coordinator.data.effects]

    @property
    def is_on(self) -> bool:
        """Return the state of the light."""
        state = self.coordinator.data.state

        # If there is no master, we take the master state into account
        # on the segment level.
        if not self.coordinator.has_master_light and not state.on:
            return False

        return bool(state.segments[self._segment].on)

    @wled_exception_handler
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        transition = None
        if ATTR_TRANSITION in kwargs:
            # WLED uses 100ms per unit, so 10 = 1 second.
            transition = round(kwargs[ATTR_TRANSITION] * 10)

        # If there is no master control, and only 1 segment, handle the
        if not self.coordinator.has_master_light:
            await self.coordinator.wled.master(on=False, transition=transition)
            return

        await self.coordinator.wled.segment(
            segment_id=self._segment, on=False, transition=transition
        )

    @wled_exception_handler
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        data: dict[str, Any] = {
            ATTR_ON: True,
            ATTR_SEGMENT_ID: self._segment,
        }

        if ATTR_RGB_COLOR in kwargs:
            data[ATTR_COLOR_PRIMARY] = kwargs[ATTR_RGB_COLOR]

        if ATTR_RGBW_COLOR in kwargs:
            data[ATTR_COLOR_PRIMARY] = kwargs[ATTR_RGBW_COLOR]

        if ATTR_TRANSITION in kwargs:
            # WLED uses 100ms per unit, so 10 = 1 second.
            data[ATTR_TRANSITION] = round(kwargs[ATTR_TRANSITION] * 10)

        if ATTR_BRIGHTNESS in kwargs:
            data[ATTR_BRIGHTNESS] = kwargs[ATTR_BRIGHTNESS]

        if ATTR_EFFECT in kwargs:
            data[ATTR_EFFECT] = kwargs[ATTR_EFFECT]

        # If there is no master control, and only 1 segment, handle the master
        if not self.coordinator.has_master_light:
            master_data = {ATTR_ON: True}
            if ATTR_BRIGHTNESS in data:
                master_data[ATTR_BRIGHTNESS] = data[ATTR_BRIGHTNESS]
                data[ATTR_BRIGHTNESS] = 255

            if ATTR_TRANSITION in data:
                master_data[ATTR_TRANSITION] = data[ATTR_TRANSITION]
                del data[ATTR_TRANSITION]

            await self.coordinator.wled.segment(**data)
            await self.coordinator.wled.master(**master_data)
            return

        await self.coordinator.wled.segment(**data)

    @wled_exception_handler
    async def async_colors(
        self,
        color_primary: tuple[int, int, int, int] | tuple[int, int, int] | None = None,
        color_secondary: tuple[int, int, int, int] | tuple[int, int, int] | None = None,
        color_tertiary: tuple[int, int, int, int] | tuple[int, int, int] | None = None,
        color_name_primary: str | None = None,
        color_name_secondary: str | None = None,
        color_name_tertiary: str | None = None,
    ) -> None:

        if color_name_primary is not None:
            color_primary = color_name_to_rgb(color_name_primary)

        if color_name_secondary is not None:
            color_secondary = color_name_to_rgb(color_name_secondary)

        if color_name_tertiary is not None:
            color_tertiary = color_name_to_rgb(color_name_tertiary)

        """Set the colors of a WLED light."""
        await self.coordinator.wled.segment(
            segment_id=self._segment,
            color_primary=color_primary,
            color_secondary=color_secondary,
            color_tertiary=color_tertiary,
        )


@callback
def async_update_segments(
    coordinator: WLEDDataUpdateCoordinator,
    current_ids: set[int],
    async_add_entities,
) -> None:
    """Update segments."""
    segment_ids = {light.segment_id for light in coordinator.data.state.segments}
    new_entities: list[WLEDMasterLight | WLEDSegmentLight] = []

    # More than 1 segment now? No master? Add master controls
    if not coordinator.keep_master_light and (
        len(current_ids) < 2 and len(segment_ids) > 1
    ):
        new_entities.append(WLEDMasterLight(coordinator))

    # Process new segments, add them to Home Assistant
    for segment_id in segment_ids - current_ids:
        current_ids.add(segment_id)
        new_entities.append(WLEDSegmentLight(coordinator, segment_id))

    if new_entities:
        async_add_entities(new_entities)
