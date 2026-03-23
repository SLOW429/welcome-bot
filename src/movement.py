from highrise.models import Position


def make_position(data: dict) -> Position:
    facing = data.get('facing', 'FrontRight')
    if hasattr(facing, 'value'):
        facing = facing.value
    elif hasattr(facing, 'name'):
        facing = facing.name

    return Position(
        float(data['x']),
        float(data['y']),
        float(data['z']),
        facing,
    )
