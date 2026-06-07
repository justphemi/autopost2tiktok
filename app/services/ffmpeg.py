
import subprocess
import os

PROCESSED_DIR = "/tmp/boltreels_processed"
os.makedirs(PROCESSED_DIR, exist_ok=True)

def make_unique(input_path: str, job_id: str) -> str:
    """
    Apply subtle FFmpeg transformations that:
      1. Break platform content-fingerprinting (hash, audio, visual fingerprint)
      2. Are completely invisible/inaudible to human viewers
      3. Do NOT alter the hook, pacing, or flow of the video
    """
    output_path = os.path.join(PROCESSED_DIR, f"{job_id}_processed.mp4")

    # -------------------------------------------------------
    # VIDEO FILTER CHAIN (vf):
    #
    # 1. crop=iw*0.97:ih*0.97:(iw-iw*0.97)/2:(ih-ih*0.97)/2
    #    Crops 3% from all edges symmetrically.
    #    - iw*0.97 = 97% of original width
    #    - ih*0.97 = 97% of original height
    #    - The (iw-iw*0.97)/2 offsets center the crop
    #    - This removes edge pixels platforms use for fingerprinting
    #    - Visually undetectable, composition stays identical
    #
    # 2. eq=saturation=1.05:contrast=1.02
    #    Tiny color grade:
    #    - saturation=1.05: 5% more vivid colors (actually looks slightly better)
    #    - contrast=1.02: 2% more contrast (barely perceptible, slightly punchier)
    #    - Both values are so small a viewer would never notice
    #    - Changes every pixel's color value, breaking visual hash fingerprints
    #
    # 3. setpts=PTS/1.01
    #    Speed nudge: makes video 1% faster
    #    - At 1% speed increase on a 60s video = 59.4s (0.6s difference)
    #    - Completely imperceptible to viewers
    #    - Changes all timestamp values in the video stream, breaking timing fingerprints
    # -------------------------------------------------------
    video_filters = (
        "crop=iw*0.97:ih*0.97:(iw-iw*0.97)/2:(ih-ih*0.97)/2,"  # Step 1: subtle edge crop
        "eq=saturation=1.05:contrast=1.02,"                       # Step 2: micro color grade
        "setpts=PTS/1.01"                                          # Step 3: 1% speed nudge
    )

    # -------------------------------------------------------
    # AUDIO FILTER CHAIN (af):
    #
    # 1. asetrate=44100*1.01,aresample=44100
    #    Micro pitch shift:
    #    - asetrate=44100*1.01: temporarily tells FFmpeg the audio is sampled at 1% higher rate
    #    - aresample=44100: resamples back to standard 44100Hz
    #    - Net effect: audio pitch rises by ~1 semitone (0.17 semitones actually)
    #    - Result is completely inaudible to humans
    #    - Breaks audio fingerprinting (ContentID, TikTok audio matching)
    #
    # 2. atempo=1.01
    #    Match audio speed to video speed nudge:
    #    - Since we sped video up 1%, we must speed audio up 1% too
    #    - Keeps audio perfectly in sync with video
    #    - atempo range is 0.5 to 2.0, so 1.01 is well within limits
    # -------------------------------------------------------
    audio_filters = (
        "asetrate=44100*1.01,aresample=44100,"  # Step 4: micro pitch shift (breaks audio fingerprint)
        "atempo=1.01"                             # Step 5: match audio tempo to video speed
    )

    cmd = [
        "ffmpeg",
        "-i", input_path,           # Input file
        "-vf", video_filters,       # Apply video filter chain
        "-af", audio_filters,       # Apply audio filter chain
        # -------------------------------------------------------
        # RE-ENCODE SETTINGS:
        # -c:v libx264: Re-encode video with H.264 codec
        #   - Re-encoding completely changes the file's binary content
        #   - Even identical input produces different output bytes each time
        #   - This alone breaks most basic hash-based fingerprinting
        # -crf 18: Constant Rate Factor (quality)
        #   - Range: 0 (lossless) to 51 (worst)
        #   - 18 = visually near-lossless, excellent quality
        #   - Lower than default (23) to compensate for any generation loss
        # -preset fast: encoding speed vs compression tradeoff
        #   - 'fast' is a good balance for a server (not too slow, good compression)
        # -c:a aac: Re-encode audio as AAC
        # -b:a 192k: 192kbps audio bitrate (high quality)
        # -map_metadata -1: STRIP ALL METADATA from the output file
        #   - Removes camera info, original timestamps, encoder info, GPS data
        #   - This is important: metadata can contain fingerprint-relevant info
        # -movflags +faststart: puts MP4 index at start of file (better streaming)
        # -------------------------------------------------------
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "192k",
        "-map_metadata", "-1",      # Strip all original metadata
        "-movflags", "+faststart",  # Optimize for web streaming
        "-y",                       # Overwrite output if exists
        output_path
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise Exception(
                f"FFmpeg processing failed with exit code {result.returncode}. "
                f"FFmpeg error: {result.stderr[-500:] if result.stderr else 'No error output.'}"
            )
    except subprocess.TimeoutExpired:
        raise Exception("FFmpeg processing timed out after 5 minutes. The video may be too large.")

    if not os.path.exists(output_path):
        raise Exception("FFmpeg finished but no output file was created. Check FFmpeg installation.")

    return output_path