"""
Script to empirically determine the relationship between
text2midi max_length parameter and actual MIDI duration in beats.

Run manually with HF_TOKEN set: python tests/test_text2midi_duration_mapping.py
Use this to build a mapping table for beats_to_max_length().
Not collected by pytest (no test_ prefix).
"""

import sys
import logging
from gradio_client import Client
import mido

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_max_length_to_beats_mapping(hf_token: str):
    """
    Test various max_length values to find the relationship to beat count.
    
    Tests:
    - Small songs (2-8 bars): 8, 16, 32 beats
    - Medium songs (8-16 bars): 32, 48, 64 beats  
    - Large songs (16-32 bars): 64, 96, 128 beats
    - Very large songs (32-64 bars): 128, 192, 256 beats
    """
    client = Client("amaai-lab/text2midi", token=hf_token)
    
    # Test a range of max_length values
    # Start conservatively, then explore
    test_max_lengths = [
        # Small range (exploring around default)
        250, 300, 400, 500, 600, 700, 800,
        # Medium range
        900, 1000, 1100, 1200, 1300, 1400, 1500,
        # Large range
        1600, 1800, 2000, 2250, 2500,
        # Very large range
        3000, 3500, 4000, 5000,
    ]
    
    prompt = "A simple piano melody in C major with a steady rhythm"
    
    results = []
    
    print("\n" + "="*80)
    print("TEXT2MIDI MAX_LENGTH → BEATS MAPPING TEST")
    print("="*80)
    print(f"\nPrompt: {prompt}")
    print(f"\nTesting {len(test_max_lengths)} max_length values...\n")
    print(f"{'max_length':>12} │ {'beats':>8} │ {'bars':>6} │ {'notes':>7}")
    print("─"*12 + "─┼─" + "─"*8 + "─┼─" + "─"*6 + "─┼─" + "─"*7)
    
    for max_len in test_max_lengths:
        try:
            result = client.predict(
                prompt,
                0.9,  # temperature
                max_len,
                api_name="/predict"
            )
            
            midi_path = result[1]
            mid = mido.MidiFile(midi_path)
            
            # Calculate duration
            total_ticks = 0
            note_count = 0
            
            for track in mid.tracks:
                track_ticks = sum(msg.time for msg in track)
                total_ticks = max(total_ticks, track_ticks)
                
                # Count notes
                for msg in track:
                    if msg.type == 'note_on' and msg.velocity > 0:
                        note_count += 1
            
            total_beats = total_ticks / mid.ticks_per_beat
            total_bars = total_beats / 4
            
            results.append({
                'max_length': max_len,
                'beats': total_beats,
                'bars': total_bars,
                'notes': note_count,
            })
            
            print(f"{max_len:12d} │ {total_beats:8.1f} │ {total_bars:6.1f} │ {note_count:7d}")
            
        except Exception as e:
            logger.error(f"max_length={max_len} failed: {e}")
            print(f"{max_len:12d} │ {'ERROR':>8} │ {'':>6} │ {'':>7}")
    
    print("\n" + "="*80)
    print("ANALYSIS")
    print("="*80)
    
    # Find good values for common beat counts
    target_beats = [8, 16, 32, 48, 64, 96, 128, 192, 256]
    
    print(f"\n{'Target Beats':>13} │ {'Target Bars':>12} │ {'Recommended max_length':>25}")
    print("─"*13 + "─┼─" + "─"*12 + "─┼─" + "─"*25)
    
    for target in target_beats:
        # Find closest result
        if results:
            closest = min(results, key=lambda x: abs(x['beats'] - target))
            print(f"{target:13d} │ {target/4:12.1f} │ {closest['max_length']:25d}")
    
    # Generate Python mapping code
    print("\n" + "="*80)
    print("SUGGESTED MAPPING FUNCTION")
    print("="*80)
    print("\ndef beats_to_max_length(beats: float) -> int:")
    print('    """Map beat count to text2midi max_length parameter."""')
    print("    # Empirically derived mapping")
    
    if results:
        # Create a simple piecewise linear mapping from the data
        sorted_results = sorted(results, key=lambda x: x['beats'])
        print("    mapping = [")
        for r in sorted_results[::3]:  # Sample every 3rd point
            print(f"        ({r['beats']:.1f}, {r['max_length']}),  # {r['bars']:.1f} bars")
        print("    ]")
        print("    ")
        print("    # Find closest match or interpolate")
        print("    if beats <= mapping[0][0]:")
        print("        return mapping[0][1]")
        print("    if beats >= mapping[-1][0]:")
        print("        return mapping[-1][1]")
        print("    ")
        print("    for i in range(len(mapping) - 1):")
        print("        b1, ml1 = mapping[i]")
        print("        b2, ml2 = mapping[i + 1]")
        print("        if b1 <= beats <= b2:")
        print("            # Linear interpolation")
        print("            ratio = (beats - b1) / (b2 - b1)")
        print("            return int(ml1 + ratio * (ml2 - ml1))")
        print("    ")
        print("    return 500  # fallback")
    
    print("\n" + "="*80)
    return results


if __name__ == "__main__":
    import os

    # Get HF token from environment
    hf_token = os.getenv("HF_API_KEY")
    if not hf_token:
        print("ERROR: HF_API_KEY environment variable not set")
        sys.exit(1)

    results = run_max_length_to_beats_mapping(hf_token)
    
    print(f"\n✓ Test complete. Tested {len(results)} configurations.")
    print("\nCopy the suggested mapping function into text2midi_backend.py")
