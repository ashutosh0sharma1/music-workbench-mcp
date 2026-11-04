"""Conservative FLP event edits that preserve every unrelated byte.

Supports the classic wire format and the observed FL 2025 event-172 quirk.
Rejects malformed or ambiguous files rather than guessing or using partial models.
"""
from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class Event:
    id: int
    start: int
    payload: int
    end: int


def varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _walk(raw, begin, end, quirky):
    events = []
    offset = begin
    while offset < end:
        start = offset
        eid = raw[offset]
        offset += 1
        if eid < 192:
            size = 1 if eid < 64 or (quirky and eid == 172) else 2 if eid < 128 else 4
        else:
            size = 0
            for shift in range(0, 35, 7):
                if offset >= end:
                    raise ValueError("Truncated event length")
                b = raw[offset]
                offset += 1
                size |= (b & 127) << shift
                if not b & 128:
                    break
            else:
                raise ValueError("Oversized event length")
        if offset + size > end:
            raise ValueError("Event extends past FLdt")
        events.append(Event(eid, start, offset, offset + size))
        offset += size
    return events


def parse(raw):
    if len(raw) < 22 or len(raw) > 128 * 1024 * 1024 or raw[:4] != b"FLhd":
        raise ValueError("Unsupported FLP header or size")
    header_size = struct.unpack_from("<I", raw, 4)[0]
    data_header = 8 + header_size
    if header_size < 6 or data_header + 8 > len(raw) or raw[data_header:data_header + 4] != b"FLdt":
        raise ValueError("Unsupported FLP chunk layout")
    begin = data_header + 8
    end = begin + struct.unpack_from("<I", raw, data_header + 4)[0]
    if end != len(raw):
        raise ValueError("FLdt length mismatch or unsupported trailing chunks")
    channels = struct.unpack_from("<H", raw, 10)[0]
    candidates = []
    for quirky in (False, True):
        try:
            events = _walk(raw, begin, end, quirky)
            tempos = [e for e in events if e.id == 156]
            if sum(e.id == 64 for e in events) != channels or len(tempos) != 1:
                continue
            tempo = struct.unpack_from("<I", raw, tempos[0].payload)[0]
            if not 10000 <= tempo <= 999000:
                continue
            if events not in candidates:
                candidates.append(events)
        except (ValueError, struct.error):
            continue
    if len(candidates) != 1:
        raise ValueError("Unsupported or ambiguous FLP event layout")
    return data_header, candidates[0]


def payloads(raw, eid):
    return [raw[e.payload:e.end] for e in parse(raw)[1] if e.id == eid]


def rewrite(raw, replacements):
    """Replace payloads by event ordinal; keep all other event/header bytes exact."""
    header, events = parse(raw)
    if any(i < 0 or i >= len(events) for i in replacements):
        raise ValueError("Invalid replacement index")
    pieces = []
    for i, event in enumerate(events):
        if i not in replacements:
            pieces.append(raw[event.start:event.end])
            continue
        data = replacements[i]
        if event.id < 192:
            if len(data) != event.end - event.payload:
                raise ValueError("Fixed-width event replacement has wrong length")
            pieces.append(raw[event.start:event.payload] + data)
        else:
            pieces.append(bytes([event.id]) + varint(len(data)) + data)
    body = b"".join(pieces)
    result = raw[:header + 4] + struct.pack("<I", len(body)) + body
    parse(result)
    return result


def change_tempo(raw, bpm):
    if not 10 <= bpm <= 999:
        raise ValueError("Invalid tempo")
    index = next(i for i, e in enumerate(parse(raw)[1]) if e.id == 156)
    return rewrite(raw, {index: struct.pack("<I", round(bpm * 1000))})


def sample_references(raw):
    return [p.decode("utf-16-le").rstrip("\0") for p in payloads(raw, 196) if p]


def relink_samples(raw, mapping):
    def key(path):
        return path.replace("\\", "/").casefold()
    mapping = {key(k): str(v) for k, v in mapping.items()}
    changes = {}
    for i, event in enumerate(parse(raw)[1]):
        if event.id == 196:
            old = raw[event.payload:event.end].decode("utf-16-le").rstrip("\0")
            if key(old) in mapping:
                changes[i] = (mapping[key(old)] + "\0").encode("utf-16-le")
    return rewrite(raw, changes)
