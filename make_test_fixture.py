"""Create an eight-second synthetic tone for offline workbench regression tests."""
from pathlib import Path
import math
import struct
import wave

destination = Path(__file__).resolve().parent / "runtime/synthetic-test.wav"
destination.parent.mkdir(parents=True, exist_ok=True)
if destination.exists():
    raise SystemExit("Fixture already exists; refusing to overwrite.")
rate = 44100
with wave.open(str(destination), "wb") as output:
    output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
    output.writeframes(b"".join(struct.pack("<h", round(3200 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(rate * 8)))
print("Created runtime/synthetic-test.wav")
