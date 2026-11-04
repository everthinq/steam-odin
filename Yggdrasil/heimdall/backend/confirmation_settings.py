"""The Global Confirmations settings: what they are, their limits, and the check.

One source for the confirmation check interval limits (the scheduler and the
settings window both read them here), and the explanation the window shows.
A value outside the limits is refused with {error, code}; nothing is changed
silently.
"""

CHECK_INTERVAL_MIN_SECONDS = 10
CHECK_INTERVAL_MAX_SECONDS = 3600
CHECK_INTERVAL_STEP_SECONDS = 5

SWITCH_KEYS = ('auto_check_enabled', 'auto_confirm_market', 'auto_confirm_trades')
KEYS = ('check_interval',) + SWITCH_KEYS


class SettingsError(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def polling_on(settings):
    """True when the background loop checks confirmations (any switch on)."""
    return any(bool(settings.get(key)) for key in SWITCH_KEYS)


def polling_note(settings):
    """One plain sentence on what the loop does with these settings."""
    seconds = settings.get('check_interval')
    auto_confirm = settings.get('auto_confirm_market') or settings.get('auto_confirm_trades')
    if not polling_on(settings):
        return 'Heimdall does not check confirmations by itself. Use Check All Now.'
    if auto_confirm and not settings.get('auto_check_enabled'):
        return (f'Auto-confirm is on, so Heimdall checks every {seconds} seconds even with '
                'Automatic Checking off.')
    return f'Heimdall checks every {seconds} seconds.'


def view(settings):
    """What the settings window shows: the values, the limits and the note."""
    values = {key: settings.get(key) for key in KEYS}
    return {
        'settings': values,
        'limits': {'check_interval': {
            'min': CHECK_INTERVAL_MIN_SECONDS,
            'max': CHECK_INTERVAL_MAX_SECONDS,
            'step': CHECK_INTERVAL_STEP_SECONDS,
        }},
        'polling_on': polling_on(values),
        'polling_note': polling_note(values),
    }


def clean(body):
    """Only the confirmation keys, checked. Raises SettingsError on a bad value."""
    if not isinstance(body, dict):
        raise SettingsError('Send the settings as a JSON object.', 'bad_body')
    cleaned = {}
    if 'check_interval' in body:
        value = body['check_interval']
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsError('The check interval must be a whole number of seconds.', 'bad_interval')
        if not CHECK_INTERVAL_MIN_SECONDS <= value <= CHECK_INTERVAL_MAX_SECONDS:
            raise SettingsError(
                f'The check interval must be between {CHECK_INTERVAL_MIN_SECONDS} and '
                f'{CHECK_INTERVAL_MAX_SECONDS} seconds.', 'interval_out_of_range')
        cleaned['check_interval'] = value
    for key in SWITCH_KEYS:
        if key in body:
            if not isinstance(body[key], bool):
                raise SettingsError(f'{key} must be true or false.', 'bad_switch')
            cleaned[key] = body[key]
    if not cleaned:
        raise SettingsError('Nothing to save.', 'nothing_to_save')
    return cleaned


def loop_interval(settings):
    """Seconds the background loop sleeps: the setting, kept inside the limits
    (an old settings.json may hold a value saved before the limits existed)."""
    value = settings.get('check_interval')
    if isinstance(value, bool) or not isinstance(value, int):
        value = CHECK_INTERVAL_MAX_SECONDS
    return min(CHECK_INTERVAL_MAX_SECONDS, max(CHECK_INTERVAL_MIN_SECONDS, value))
