"""
MIDI Tokenization using REMI (Revamped MIDI) representation.

REMI encodes MIDI as a sequence of tokens representing:
- Bar boundaries
- Beat positions within bars
- Note events (pitch, duration, velocity)
- Chord symbols (optional)
- Tempo (optional)

This tokenizer is bidirectional: MIDI → tokens → MIDI

See NEURAL_MIDI_ROADMAP.md for full specification.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class TokenType(Enum):
    """REMI token types."""
    BAR = "BAR"
    POSITION = "POS"
    PITCH = "PITCH"
    DURATION = "DUR"
    VELOCITY = "VEL"
    CHORD = "CHORD"
    TEMPO = "TEMPO"
    PAD = "PAD"
    BOS = "BOS"  # Beginning of sequence
    EOS = "EOS"  # End of sequence


@dataclass
class TokenizerConfig:
    """Configuration for REMI tokenizer."""
    
    # Position resolution: positions per bar (16 = 16th note grid)
    positions_per_bar: int = 16
    
    # Pitch range
    min_pitch: int = 21  # A0
    max_pitch: int = 108  # C8
    
    # Duration quantization (in positions)
    duration_bins: list[int] = field(default_factory=lambda: [1, 2, 3, 4, 6, 8, 12, 16, 24, 32])
    
    # Velocity quantization
    velocity_bins: list[int] = field(default_factory=lambda: [16, 32, 48, 64, 80, 96, 112, 127])
    
    # Special tokens
    pad_token: str = "<PAD>"
    bos_token: str = "<BOS>"
    eos_token: str = "<EOS>"
    
    # Max sequence length
    max_seq_length: int = 1024


@dataclass
class Token:
    """A single REMI token."""
    type: TokenType
    value: Optional[int] = None
    
    def to_string(self) -> str:
        if self.value is not None:
            return f"{self.type.value}_{self.value}"
        return self.type.value
    
    @classmethod
    def from_string(cls, s: str) -> Token:
        if "_" in s:
            type_str, value_str = s.rsplit("_", 1)
            return cls(type=TokenType(type_str), value=int(value_str))
        return cls(type=TokenType(s))


class MidiTokenizer:
    """
    REMI tokenizer for MIDI note sequences.
    
    Converts between note lists and token sequences.
    """
    
    def __init__(self, config: Optional[TokenizerConfig] = None):
        self.config = config or TokenizerConfig()
        self._build_vocab()
    
    def _build_vocab(self):
        """Build vocabulary mapping."""
        self.token_to_id: dict[str, int] = {}
        self.id_to_token: dict[int, str] = {}
        
        idx = 0
        
        # Special tokens
        for special in [self.config.pad_token, self.config.bos_token, self.config.eos_token]:
            self.token_to_id[special] = idx
            self.id_to_token[idx] = special
            idx += 1
        
        # BAR token
        self.token_to_id["BAR"] = idx
        self.id_to_token[idx] = "BAR"
        idx += 1
        
        # Position tokens (0 to positions_per_bar - 1)
        for pos in range(self.config.positions_per_bar):
            token = f"POS_{pos}"
            self.token_to_id[token] = idx
            self.id_to_token[idx] = token
            idx += 1
        
        # Pitch tokens
        for pitch in range(self.config.min_pitch, self.config.max_pitch + 1):
            token = f"PITCH_{pitch}"
            self.token_to_id[token] = idx
            self.id_to_token[idx] = token
            idx += 1
        
        # Duration tokens
        for dur in self.config.duration_bins:
            token = f"DUR_{dur}"
            self.token_to_id[token] = idx
            self.id_to_token[idx] = token
            idx += 1
        
        # Velocity tokens
        for vel in self.config.velocity_bins:
            token = f"VEL_{vel}"
            self.token_to_id[token] = idx
            self.id_to_token[idx] = token
            idx += 1
        
        self.vocab_size = idx
        logger.info(f"REMI tokenizer vocab size: {self.vocab_size}")
    
    def _quantize_duration(self, duration_beats: float, tempo: int = 120) -> int:
        """Quantize duration to nearest bin (in positions)."""
        # Convert beats to positions
        positions = duration_beats * (self.config.positions_per_bar / 4)  # 4 beats per bar
        
        # Find nearest bin
        best_bin = self.config.duration_bins[0]
        best_diff = abs(positions - best_bin)
        
        for bin_val in self.config.duration_bins:
            diff = abs(positions - bin_val)
            if diff < best_diff:
                best_diff = diff
                best_bin = bin_val
        
        return best_bin
    
    def _quantize_velocity(self, velocity: int) -> int:
        """Quantize velocity to nearest bin."""
        best_bin = self.config.velocity_bins[0]
        best_diff = abs(velocity - best_bin)
        
        for bin_val in self.config.velocity_bins:
            diff = abs(velocity - bin_val)
            if diff < best_diff:
                best_diff = diff
                best_bin = bin_val
        
        return best_bin
    
    def encode(
        self,
        notes: list[dict],
        bars: int,
        add_special_tokens: bool = True,
    ) -> list[int]:
        """
        Encode a list of notes to REMI token IDs.
        
        Args:
            notes: List of {pitch, start_beat, duration_beats, velocity}
            bars: Number of bars in the sequence
            add_special_tokens: Whether to add BOS/EOS tokens
            
        Returns:
            List of token IDs
        """
        tokens: list[str] = []
        
        if add_special_tokens:
            tokens.append(self.config.bos_token)
        
        # Sort notes by start time
        sorted_notes = sorted(notes, key=lambda n: (n["start_beat"], n["pitch"]))
        
        # Group notes by bar
        for bar_idx in range(bars):
            bar_start = bar_idx * 4  # 4 beats per bar
            bar_end = bar_start + 4
            
            # Add bar token
            tokens.append("BAR")
            
            # Get notes in this bar
            bar_notes = [
                n for n in sorted_notes
                if bar_start <= n["start_beat"] < bar_end
            ]
            
            # Sort by position within bar
            bar_notes.sort(key=lambda n: n["start_beat"])
            
            for note in bar_notes:
                # Position within bar (0 to positions_per_bar - 1)
                beat_in_bar = note["start_beat"] - bar_start
                position = int(beat_in_bar * (self.config.positions_per_bar / 4))
                position = min(position, self.config.positions_per_bar - 1)
                
                tokens.append(f"POS_{position}")
                
                # Pitch
                pitch = int(note["pitch"])
                pitch = max(self.config.min_pitch, min(self.config.max_pitch, pitch))
                tokens.append(f"PITCH_{pitch}")
                
                # Duration
                duration = self._quantize_duration(note.get("duration_beats", 0.5))
                tokens.append(f"DUR_{duration}")
                
                # Velocity
                velocity = self._quantize_velocity(note.get("velocity", 80))
                tokens.append(f"VEL_{velocity}")
        
        if add_special_tokens:
            tokens.append(self.config.eos_token)
        
        # Convert to IDs
        token_ids = [self.token_to_id.get(t, 0) for t in tokens]
        
        return token_ids
    
    def decode(
        self,
        token_ids: list[int],
        tempo: int = 120,
    ) -> list[dict]:
        """
        Decode REMI token IDs back to notes.
        
        Args:
            token_ids: List of token IDs
            tempo: Tempo for timing conversion
            
        Returns:
            List of {pitch, start_beat, duration_beats, velocity}
        """
        notes: list[dict] = []
        
        current_bar = -1
        current_position = 0
        current_pitch = None
        current_duration = 4  # Default quarter note
        current_velocity = 80
        
        for token_id in token_ids:
            token_str = self.id_to_token.get(token_id, "")
            
            # Skip special tokens
            if token_str in [self.config.pad_token, self.config.bos_token, self.config.eos_token]:
                continue
            
            if token_str == "BAR":
                current_bar += 1
                current_position = 0
            
            elif token_str.startswith("POS_"):
                current_position = int(token_str.split("_")[1])
            
            elif token_str.startswith("PITCH_"):
                current_pitch = int(token_str.split("_")[1])
            
            elif token_str.startswith("DUR_"):
                current_duration = int(token_str.split("_")[1])
            
            elif token_str.startswith("VEL_"):
                current_velocity = int(token_str.split("_")[1])
                
                # We have a complete note (velocity is the last token in a note)
                if current_pitch is not None and current_bar >= 0:
                    # Convert position to beats
                    beat_in_bar = current_position * 4 / self.config.positions_per_bar
                    start_beat = current_bar * 4 + beat_in_bar
                    
                    # Convert duration positions to beats
                    duration_beats = current_duration * 4 / self.config.positions_per_bar
                    
                    notes.append({
                        "pitch": current_pitch,
                        "start_beat": start_beat,
                        "duration_beats": duration_beats,
                        "velocity": current_velocity,
                    })
                    
                    current_pitch = None
        
        return notes
    
    def encode_to_tokens(self, notes: list[dict], bars: int) -> list[str]:
        """Encode notes to token strings (for debugging)."""
        token_ids = self.encode(notes, bars, add_special_tokens=False)
        return [self.id_to_token.get(tid, "<UNK>") for tid in token_ids]
    
    def get_vocab_size(self) -> int:
        """Return vocabulary size."""
        return self.vocab_size
    
    def get_pad_token_id(self) -> int:
        """Return PAD token ID."""
        return self.token_to_id[self.config.pad_token]
    
    def get_bos_token_id(self) -> int:
        """Return BOS token ID."""
        return self.token_to_id[self.config.bos_token]
    
    def get_eos_token_id(self) -> int:
        """Return EOS token ID."""
        return self.token_to_id[self.config.eos_token]
