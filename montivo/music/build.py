"""Build the bundled music beds: `python3 montivo/music/build.py` writes the loops (.m4a) and tracks.json.

Every track is synthesised here from scratch (FM electric piano, additive pads, sine bass, a sine-sweep kick, noise
hats and claps), seeded, so the same code always gives the same files and nobody else holds a right in them: they
are dedicated to the public domain (CC0 1.0, see README.md). No sample, preset or recording of anyone else is used.

Each pattern is rendered twice back to back and the second pass is kept: its start already carries the tails of the
pass before it, so the file loops with no seam. tracks.json records each loop's length, tempo and integrated
loudness (ffmpeg ebur128), which the render uses to set the bed's level without measuring again.
"""
from __future__ import annotations

import array
import json
import math
import random
import subprocess
import sys
import wave
from pathlib import Path

SR = 48000
HERE = Path(__file__).resolve().parent
TAU = 2 * math.pi

NOTE = {n: i for i, n in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}


def hz(name: str) -> float:
    """'A4' -> 440.0, 'C#3' -> 138.6."""
    pitch, octave = name[:-1], int(name[-1])
    return 440.0 * 2 ** ((NOTE[pitch] + 12 * (octave + 1) - 69) / 12)


class Bus:
    """A stereo buffer of floats; voices add into it at sample offsets."""

    def __init__(self, seconds: float):
        self.n = int(seconds * SR)
        self.l = [0.0] * self.n
        self.r = [0.0] * self.n

    def add(self, start_s: float, mono: list[float], gain: float = 1.0, pan: float = 0.0) -> None:
        k0 = int(start_s * SR)
        gl, gr = gain * math.cos((pan + 1) * math.pi / 4), gain * math.sin((pan + 1) * math.pi / 4)
        for i, x in enumerate(mono):
            k = k0 + i
            if k >= self.n:
                break
            self.l[k] += x * gl
            self.r[k] += x * gr

    def echo(self, delay_s: float, feedback: float, mix: float, cross: bool = True) -> None:
        """A feedback delay over the whole buffer (ping-pong when cross)."""
        d = int(delay_s * SR)
        l, r = self.l[:], self.r[:]
        for k in range(d, self.n):
            if cross:
                l[k] += r[k - d] * feedback
                r[k] += l[k - d] * feedback
            else:
                l[k] += l[k - d] * feedback
                r[k] += r[k - d] * feedback
        for k in range(self.n):
            self.l[k] += (l[k] - self.l[k]) * mix
            self.r[k] += (r[k] - self.r[k]) * mix

    def mix_in(self, other: "Bus", gain: float = 1.0) -> None:
        for k in range(min(self.n, other.n)):
            self.l[k] += other.l[k] * gain
            self.r[k] += other.r[k] * gain


def env(n: int, attack_s: float, decay_s: float, release_at: int | None = None, release_s: float = 0.1) -> list[float]:
    """Attack ramp, exponential decay (time constant decay_s), optional release ramp from sample release_at."""
    a = max(1, int(attack_s * SR))
    out = []
    for i in range(n):
        v = (i / a if i < a else 1.0) * math.exp(-max(0, i - a) / (decay_s * SR))
        if release_at is not None and i >= release_at:
            v *= max(0.0, 1.0 - (i - release_at) / (release_s * SR))
        out.append(v)
    return out


def epiano(f: float, dur_s: float, index: float = 1.6, ratio: float = 1.0, decay_s: float = 1.2) -> list[float]:
    """FM electric piano: a sine carrier, a modulator at `ratio` whose index falls fast (the bark of the attack)."""
    n = int((dur_s + 0.6) * SR)
    e = env(n, 0.003, decay_s, int(dur_s * SR), 0.5)
    out = []
    for i in range(n):
        t = i / SR
        idx = index * math.exp(-t / 0.25) + 0.15
        out.append(e[i] * math.sin(TAU * f * t + idx * math.sin(TAU * f * ratio * t)))
    return out


def pad(f: float, dur_s: float, detune_cents: float = 5.0) -> tuple[list[float], list[float]]:
    """A soft additive pad, two voices detuned against each other, one per side; slow attack and release."""
    n = int((dur_s + 1.2) * SR)
    rel = int(dur_s * SR)
    a = max(1, int(0.6 * SR))
    sides = []
    for c in (-detune_cents, detune_cents):
        g = f * 2 ** (c / 1200)
        side = []
        for i in range(n):
            t = i / SR
            v = min(1.0, i / a) * (1.0 if i < rel else max(0.0, 1 - (i - rel) / (1.2 * SR)))
            side.append(v * (math.sin(TAU * g * t) + 0.35 * math.sin(TAU * 2 * g * t + 0.4)
                             + 0.12 * math.sin(TAU * 3 * g * t + 1.1)))
        sides.append(side)
    return sides[0], sides[1]


def bass(f: float, dur_s: float) -> list[float]:
    n = int((dur_s + 0.08) * SR)
    e = env(n, 0.005, 0.9, int(dur_s * SR), 0.08)
    return [e[i] * (math.sin(TAU * f * i / SR) + 0.25 * math.sin(TAU * 2 * f * i / SR)) for i in range(n)]


def kick() -> list[float]:
    n = int(0.35 * SR)
    out, ph = [], 0.0
    for i in range(n):
        t = i / SR
        ph += TAU * (45 + 75 * math.exp(-t / 0.03)) / SR
        out.append(math.exp(-t / 0.12) * math.sin(ph) * min(1.0, i / 40))
    return out


def noise_hit(rng: random.Random, dur_s: float, decay_s: float, bright: float = 0.85) -> list[float]:
    """White noise through a one-pole high-pass (bright 0..1), exponential decay: hats, shakers."""
    n = int(dur_s * SR)
    out, prev_in, prev_out = [], 0.0, 0.0
    for i in range(n):
        x = rng.uniform(-1, 1)
        y = bright * (prev_out + x - prev_in)
        prev_in, prev_out = x, y
        out.append(y * math.exp(-i / (decay_s * SR)))
    return out


def clap(rng: random.Random) -> list[float]:
    """Three quick noise bursts then a tail, band-limited by averaging: a hand clap."""
    n = int(0.3 * SR)
    out, lp = [], 0.0
    for i in range(n):
        t = i / SR
        burst = max(math.exp(-(t - o) / 0.006) if t >= o else 0.0 for o in (0.0, 0.011, 0.022))
        tail = math.exp(-t / 0.07) * 0.6
        lp += 0.45 * (rng.uniform(-1, 1) - lp)
        out.append(lp * max(burst, tail) * 1.6)
    return out


def chord(names: str) -> list[float]:
    return [hz(x) for x in names.split()]


# --- the tracks -------------------------------------------------------------------------------------------------

def low_light(rng: random.Random) -> tuple[Bus, float, float]:
    """Lo-fi: FM piano on ii-V-I-vi with a lazy swing, sine bass, soft kick and rim, 80 bpm."""
    bpm, bars = 80, 8
    beat = 60 / bpm
    L = bars * 4 * beat
    bus = Bus(2 * L + 2)
    keys, drums = Bus(2 * L + 2), Bus(2 * L + 2)
    prog = [("D3 F3 A3 C4 E4", "D2"), ("G2 F3 B3 E4", "G1"), ("C3 E3 G3 B3 D4", "C2"), ("A2 G3 C4 E4", "A1")]
    for rep in range(2):
        for bar in range(bars):
            t0 = rep * L + bar * 4 * beat
            notes, root = prog[bar % 4]
            for j, f in enumerate(chord(notes)):  # a slight strum
                keys.add(t0 + j * 0.012, epiano(f, 2.2 * beat, index=1.3), 0.11, pan=-0.3 + 0.15 * j)
            keys.add(t0 + 2.5 * beat, epiano(chord(notes)[-1] * 2, 0.4 * beat, index=1.0, decay_s=0.5), 0.06, pan=0.4)
            bus.add(t0, bass(hz(root), 1.4 * beat), 0.30)
            bus.add(t0 + 2.5 * beat, bass(hz(root) * 1.5, 0.8 * beat), 0.22)
            for b in range(4):
                if b in (0,) or (b == 2 and bar % 2):
                    drums.add(t0 + b * beat, kick(), 0.55)
                if b == 2 and not bar % 2:
                    drums.add(t0 + 2.5 * beat, kick(), 0.4)
                if b in (1, 3):
                    drums.add(t0 + b * beat, noise_hit(rng, 0.12, 0.03, 0.6), 0.22, pan=0.1)
                for s in (0.0, 0.62):  # swung eighths
                    drums.add(t0 + (b + s) * beat, noise_hit(rng, 0.06, 0.012), 0.07 if s else 0.1, pan=0.35)
    keys.echo(0.75 * beat, 0.35, 0.3)
    bus.mix_in(keys)
    bus.mix_in(drums)
    return bus, L, bpm


def forward(rng: random.Random) -> tuple[Bus, float, float]:
    """Upbeat: bright FM pluck arpeggio in sixteenths over Am-F-C-G, four-on-the-floor kick, offbeat hats, 112 bpm."""
    bpm, bars = 112, 8
    beat = 60 / bpm
    L = bars * 4 * beat
    bus, arp, padb = Bus(2 * L + 2), Bus(2 * L + 2), Bus(2 * L + 2)
    prog = ["A3 C4 E4 A4", "F3 A3 C4 F4", "C4 E4 G4 C5", "G3 B3 D4 G4"]
    roots = ["A1", "F1", "C2", "G1"]
    order = [0, 1, 2, 3, 2, 1, 2, 3]
    for rep in range(2):
        for bar in range(bars):
            t0 = rep * L + bar * 4 * beat
            ch = chord(prog[bar % 4])
            for s in range(16):
                f = ch[order[s % 8]] * (2 if s >= 8 and bar % 2 else 1)
                arp.add(t0 + s * beat / 4, epiano(f, 0.18 * beat, index=2.4, ratio=2.0, decay_s=0.25), 0.07,
                        pan=-0.5 + (s % 4) / 3)
            pl, pr = pad(ch[0], 3.8 * beat)
            # the pad ducks on every kick, the pumping of a sidechained synth
            pump = [0.35 + 0.65 * min(1.0, ((i / SR) % beat) / (0.45 * beat)) for i in range(len(pl))]
            padb.add(t0, [a * p for a, p in zip(pl, pump)], 0.05, pan=-0.8)
            padb.add(t0, [a * p for a, p in zip(pr, pump)], 0.05, pan=0.8)
            for b in range(4):
                bus.add(t0 + b * beat, kick(), 0.6)
                bus.add(t0 + (b + 0.5) * beat, bass(hz(roots[bar % 4]), 0.4 * beat), 0.28)
                bus.add(t0 + (b + 0.5) * beat, noise_hit(rng, 0.09, 0.02), 0.1, pan=0.3)
                if b in (1, 3):
                    bus.add(t0 + b * beat, clap(rng), 0.16, pan=-0.1)
    arp.echo(0.75 * beat, 0.3, 0.25)
    bus.mix_in(arp)
    bus.mix_in(padb)
    return bus, L, bpm


def still_air(rng: random.Random) -> tuple[Bus, float, float]:
    """Ambient: wide pads C-Am-F-G, a sparse bell line with a long echo, no drums, 70 bpm."""
    bpm, bars = 70, 8
    beat = 60 / bpm
    L = bars * 4 * beat
    bus, bells = Bus(2 * L + 3), Bus(2 * L + 3)
    prog = ["C3 G3 E4", "A2 E3 C4", "F2 C3 A3", "G2 D3 B3"]
    line = ["E5", "G5", "C5", "D5", "A4", "C5", "E5", "D5"]
    for rep in range(2):
        for bar in range(bars):
            t0 = rep * L + bar * 4 * beat
            for f in chord(prog[bar % 4]):
                pl, pr = pad(f, 4 * beat, detune_cents=7)
                bus.add(t0, pl, 0.07, pan=-0.9)
                bus.add(t0, pr, 0.07, pan=0.9)
            bus.add(t0, bass(hz(prog[bar % 4].split()[0]) / 2, 3.6 * beat), 0.14)
            bells.add(t0 + (1.5 if bar % 2 else 0.5) * beat, epiano(hz(line[bar]), 0.5 * beat, index=3.0, ratio=3.5,
                                                                    decay_s=1.0), 0.06, pan=rng.uniform(-0.5, 0.5))
    bells.echo(1.5 * beat, 0.45, 0.45)
    bus.mix_in(bells)
    return bus, L, bpm


def snap(rng: random.Random) -> tuple[Bus, float, float]:
    """Bouncy pop: marimba-like FM hook, claps on two and four, a syncopated bass, 96 bpm."""
    bpm, bars = 96, 8
    beat = 60 / bpm
    L = bars * 4 * beat
    bus, mar = Bus(2 * L + 2), Bus(2 * L + 2)
    roots = ["D2", "B1", "G1", "A1"]
    hooks = [["F#4", "A4", "D5", "A4"], ["F#4", "B4", "D5", "B4"], ["G4", "B4", "D5", "E5"], ["E4", "A4", "C#5", "A4"]]
    hits = [0.0, 0.75, 1.5, 2.5]
    for rep in range(2):
        for bar in range(bars):
            t0 = rep * L + bar * 4 * beat
            for h, name in zip(hits, hooks[bar % 4]):
                mar.add(t0 + h * beat, epiano(hz(name), 0.3 * beat, index=2.0, ratio=4.0, decay_s=0.18), 0.12,
                        pan=-0.3 + 0.2 * hits.index(h))
            for h in (0.0, 0.75, 2.0, 3.5):
                bus.add(t0 + h * beat, bass(hz(roots[bar % 4]), 0.45 * beat), 0.3)
            for b in range(4):
                if b in (0, 2) or (b == 3 and bar % 2):
                    bus.add(t0 + (b + (0.5 if b == 3 else 0)) * beat, kick(), 0.55)
                if b in (1, 3):
                    bus.add(t0 + b * beat, clap(rng), 0.2)
                for s in (0.0, 0.5):
                    bus.add(t0 + (b + s) * beat, noise_hit(rng, 0.05, 0.01), 0.06 if s else 0.09, pan=0.4)
    mar.echo(0.5 * beat, 0.25, 0.2)
    bus.mix_in(mar)
    return bus, L, bpm


TRACKS = [
    ("low_light", "Low Light", "lo-fi keys, 80 bpm", low_light),
    ("forward", "Forward", "upbeat synth pluck, 112 bpm", forward),
    ("still_air", "Still Air", "ambient pads, no drums, 70 bpm", still_air),
    ("snap", "Snap", "bouncy marimba and claps, 96 bpm", snap),
]


def loop_of(bus: Bus, L: float) -> tuple[list[float], list[float]]:
    """The second pass of the pattern (see the module docstring), soft-clipped and scaled to -1 dBFS sample peak."""
    k0, k1 = int(L * SR), int(2 * L * SR)
    l, r = bus.l[k0:k1], bus.r[k0:k1]
    peak = max(max(abs(x) for x in l), max(abs(x) for x in r)) or 1.0
    g = 1.6 / peak
    l = [math.tanh(x * g) for x in l]
    r = [math.tanh(x * g) for x in r]
    peak = max(max(abs(x) for x in l), max(abs(x) for x in r))
    s = 10 ** (-1 / 20) / peak
    return [x * s for x in l], [x * s for x in r]


def write_wav(path: Path, l: list[float], r: list[float]) -> None:
    pcm = array.array("h", (int(max(-1.0, min(1.0, v)) * 32767) for pair in zip(l, r) for v in pair))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main() -> int:
    sys.path.insert(0, str(HERE.parent.parent))
    from montivo.engine.render import measure_loudness

    manifest = []
    for tid, name, mood, fn in TRACKS:
        rng = random.Random(f"montivo-{tid}")
        bus, L, bpm = fn(rng)
        l, r = loop_of(bus, L)
        wav = HERE / f"{tid}.wav"
        out = HERE / f"{tid}.m4a"
        write_wav(wav, l, r)
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav), "-c:a", "aac", "-b:a", "160k",
                        "-map_metadata", "-1", "-movflags", "+faststart", str(out)], check=True)
        wav.unlink()
        loud = measure_loudness(out) or {}
        manifest.append({"id": tid, "name": name, "mood": mood, "file": out.name, "bpm": bpm,
                         "loop_s": round(L, 4), "lufs": loud.get("integrated_lufs"), "true_peak_dbtp": loud.get("true_peak_dbtp"),
                         "licence": "CC0-1.0", "source": "synthesised by montivo/music/build.py"})
        print(f"{out.name}: {L:.2f} s, {loud.get('integrated_lufs')} LUFS, {out.stat().st_size / 1e3:.0f} kB")
    (HERE / "tracks.json").write_text(json.dumps({"tracks": manifest}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
