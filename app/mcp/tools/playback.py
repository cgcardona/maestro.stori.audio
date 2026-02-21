"""Playback control MCP tool definitions."""

PLAYBACK_TOOLS = [
    {
        "name": "stori_play",
        "description": "Start playback from current position or specified beat.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fromBeat": {"type": "number", "description": "Beat to start from (optional)"}
            }
        }
    },
    {
        "name": "stori_stop",
        "description": "Stop playback.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "stori_set_playhead",
        "description": "Move the playhead to a bar/beat or absolute time.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bar": {"type": "integer", "description": "Bar number"},
                "beat": {"type": "number", "description": "Beat position"},
                "seconds": {"type": "number", "description": "Time in seconds"}
            }
        }
    },
]
