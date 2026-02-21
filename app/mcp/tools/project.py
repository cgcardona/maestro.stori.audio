"""Project-level MCP tool definitions."""

PROJECT_TOOLS = [
    {
        "name": "stori_read_project",
        "description": """Read the current project state from the DAW.
Returns tempo, key signature, time signature, and all tracks with their regions.
Use this to understand the current composition before making changes.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_notes": {
                    "type": "boolean",
                    "description": "Whether to include individual MIDI notes (can be large)",
                    "default": False
                },
                "include_automation": {
                    "type": "boolean",
                    "description": "Whether to include automation data",
                    "default": False
                }
            }
        }
    },
    {
        "name": "stori_create_project",
        "description": "Create a new project with the specified tempo and name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Project name"
                },
                "tempo": {
                    "type": "number",
                    "description": "Tempo in BPM (40-240)",
                    "minimum": 40,
                    "maximum": 240
                },
                "keySignature": {
                    "type": "string",
                    "description": "Key signature (e.g., 'C', 'Am', 'F#m')"
                },
                "timeSignature": {
                    "type": "object",
                    "properties": {
                        "numerator": {"type": "integer", "default": 4},
                        "denominator": {"type": "integer", "default": 4}
                    }
                }
            },
            "required": ["name", "tempo"]
        }
    },
    {
        "name": "stori_set_tempo",
        "description": "Change the project tempo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tempo": {
                    "type": "number",
                    "description": "New tempo in BPM",
                    "minimum": 40,
                    "maximum": 240
                }
            },
            "required": ["tempo"]
        }
    },
    {
        "name": "stori_set_key",
        "description": "Set the project key signature (e.g. Cm, F# minor).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Key signature (e.g., 'C', 'Am', 'Bb', 'F#m', 'Cm')"
                }
            },
            "required": ["key"]
        }
    },
]
